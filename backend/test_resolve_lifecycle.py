"""Offline project preservation regressions; never connect to Resolve."""
import unittest
from unittest.mock import Mock

from resolve_lifecycle import preserve_current


class Project:
    def __init__(self, name='Untitled Project', identifier='existing-project'):
        self.name = name
        self.identifier = identifier
        self.contents = {'timelines': ['user edit'], 'media': ['user clip'], 'settings': {'fps': 24}}
        self.SetName = Mock(side_effect=self.rename)

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.identifier

    def rename(self, name):
        self.name = name
        return True


def manager(project=None, names=()):
    pm = Mock()
    pm.GetCurrentProject.return_value = project
    pm.GetProjectListInCurrentFolder.return_value = list(names)
    pm.SaveProject.return_value = True
    return pm


class PreservationTests(unittest.TestCase):
    def assert_no_switch(self, pm):
        for method in ('CreateProject', 'CloseProject', 'DeleteProject', 'LoadProject'):
            getattr(pm, method).assert_not_called()

    def test_no_current_project_does_not_save_or_read_library(self):
        pm = manager()
        result = preserve_current(pm, '20261023_素材')
        self.assertEqual(result['action'], 'none')
        self.assertFalse(result['saved'])
        pm.SaveProject.assert_not_called()
        pm.GetProjectListInCurrentFolder.assert_not_called()
        self.assert_no_switch(pm)

    def test_all_recognized_untitled_names_are_preserved_before_save(self):
        for name in ('Untitled Project', 'Untitled', '未命名项目', '未命名', '  Untitled Project  '):
            with self.subTest(name=name):
                project = Project(name)
                original_contents = repr(project.contents)
                pm = manager(project)
                pm.SaveProject.side_effect = lambda: project.name == '20261023_素材_原未命名项目保留'
                result = preserve_current(pm, '20261023_素材')
                project.SetName.assert_called_once_with('20261023_素材_原未命名项目保留')
                pm.SaveProject.assert_called_once_with()
                self.assertEqual(result['action'], 'renamed-and-saved')
                self.assertEqual(result['originalName'], name)
                self.assertEqual(result['preservedName'], project.name)
                self.assertTrue(result['saved'])
                self.assertEqual(result['projectId'], 'existing-project')
                self.assertEqual(repr(project.contents), original_contents)
                self.assert_no_switch(pm)

    def test_named_user_project_is_never_renamed_even_if_not_in_current_folder(self):
        project = Project('客户未保存的精剪')
        pm = manager(project, ['另一项目'])
        result = preserve_current(pm, '20261023_素材')
        project.SetName.assert_not_called()
        pm.SaveProject.assert_called_once_with()
        self.assertEqual(result['action'], 'saved-existing')
        self.assertEqual(result['preservedName'], '客户未保存的精剪')
        self.assert_no_switch(pm)

    def test_persisted_untitled_name_is_treated_as_existing_named_project(self):
        project = Project('Untitled Project')
        pm = manager(project, ['Untitled Project'])
        result = preserve_current(pm, '20261023_素材')
        project.SetName.assert_not_called()
        pm.SaveProject.assert_called_once_with()
        self.assertFalse(result['renamed'])
        self.assert_no_switch(pm)

    def test_collision_uses_numbered_unique_preservation_name(self):
        project = Project('未命名')
        pm = manager(project, ['20261023_素材_原未命名项目保留', '20261023_素材_原未命名项目保留_2'])
        result = preserve_current(pm, '20261023_素材')
        self.assertEqual(result['preservedName'], '20261023_素材_原未命名项目保留_3')
        self.assert_no_switch(pm)

    def test_library_failure_prevents_rename_save_and_switch(self):
        for name in ('Untitled Project', '客户项目'):
            with self.subTest(name=name):
                project = Project(name)
                pm = manager(project)
                pm.GetProjectListInCurrentFolder.side_effect = RuntimeError('database unavailable')
                with self.assertRaisesRegex(RuntimeError, '项目库'):
                    preserve_current(pm, '20261023_素材')
                project.SetName.assert_not_called()
                pm.SaveProject.assert_not_called()
                self.assert_no_switch(pm)

    def test_invalid_library_response_is_not_assumed_empty(self):
        for value in (None, False, {}, '项目', [None]):
            with self.subTest(value=value):
                project = Project()
                pm = manager(project)
                pm.GetProjectListInCurrentFolder.return_value = value
                with self.assertRaisesRegex(RuntimeError, '项目库'):
                    preserve_current(pm, '20261023_素材')
                project.SetName.assert_not_called()
                pm.SaveProject.assert_not_called()
                self.assert_no_switch(pm)

    def test_rename_rejected_or_raised_never_calls_save(self):
        for error in (False, RuntimeError('name rejected')):
            with self.subTest(error=error):
                project = Project()
                project.SetName.side_effect = error if isinstance(error, Exception) else None
                project.SetName.return_value = error
                pm = manager(project)
                with self.assertRaisesRegex(RuntimeError, '保留名称'):
                    preserve_current(pm, '20261023_素材')
                pm.SaveProject.assert_not_called()
                self.assert_no_switch(pm)

    def test_successful_rename_return_with_wrong_readback_does_not_save(self):
        project = Project()
        project.SetName.side_effect = None
        project.SetName.return_value = True
        pm = manager(project)
        with self.assertRaisesRegex(RuntimeError, '回读不一致'):
            preserve_current(pm, '20261023_素材')
        pm.SaveProject.assert_not_called()
        self.assert_no_switch(pm)

    def test_save_failure_does_not_close_discard_or_rename_back(self):
        for name in ('Untitled Project', '客户项目'):
            for error in (False, RuntimeError('disk full')):
                with self.subTest(name=name, error=error):
                    project = Project(name)
                    contents = repr(project.contents)
                    pm = manager(project)
                    pm.SaveProject.side_effect = error if isinstance(error, Exception) else None
                    pm.SaveProject.return_value = error
                    with self.assertRaisesRegex(RuntimeError, '保存失败'):
                        preserve_current(pm, '20261023_素材')
                    self.assertEqual(repr(project.contents), contents)
                    self.assertLessEqual(project.SetName.call_count, 1)
                    self.assert_no_switch(pm)

    def test_switch_during_library_read_does_not_save_wrong_project(self):
        project = Project('客户项目')
        other = Project('其他项目', 'different-id')
        pm = manager(project)
        pm.GetCurrentProject.side_effect = [project, other]
        with self.assertRaisesRegex(RuntimeError, '发生变化'):
            preserve_current(pm, '20261023_素材')
        pm.SaveProject.assert_not_called()
        self.assert_no_switch(pm)

    def test_switch_during_rename_does_not_save_wrong_project(self):
        project = Project()
        other = Project('其他项目', 'different-id')
        pm = manager(project)
        pm.GetCurrentProject.side_effect = [project, project, other]
        with self.assertRaisesRegex(RuntimeError, '发生变化'):
            preserve_current(pm, '20261023_素材')
        pm.SaveProject.assert_not_called()
        self.assert_no_switch(pm)

    def test_switch_during_save_blocks_caller_from_continuing(self):
        project = Project('客户项目')
        other = Project('其他项目', 'different-id')
        pm = manager(project)
        pm.GetCurrentProject.side_effect = [project, project, project, other]
        with self.assertRaisesRegex(RuntimeError, '发生变化'):
            preserve_current(pm, '20261023_素材')
        pm.SaveProject.assert_called_once_with()
        self.assert_no_switch(pm)

    def test_missing_name_stops_without_saving(self):
        for name in (None, '', '   '):
            with self.subTest(name=name):
                project = Project(name)
                pm = manager(project)
                with self.assertRaisesRegex(RuntimeError, '名称不明确'):
                    preserve_current(pm, '20261023_素材')
                project.SetName.assert_not_called()
                pm.SaveProject.assert_not_called()
                self.assert_no_switch(pm)

    def test_untitled_without_valid_incoming_name_stops(self):
        for name in (None, '', '   '):
            with self.subTest(name=name):
                project = Project()
                pm = manager(project)
                with self.assertRaisesRegex(RuntimeError, '新粗剪项目名称为空'):
                    preserve_current(pm, name)
                project.SetName.assert_not_called()
                pm.SaveProject.assert_not_called()
                self.assert_no_switch(pm)


if __name__ == '__main__':
    unittest.main()
