"""Only the personal workspace link uses native IDEA build/run; other settings survive."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace_idea_build', ROOT / 'scripts/workspace.py')
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class NativeIdeaBuildPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='IDEA native 编译 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / '.idea/gradle.xml'

    def plan(self):
        return workspace.idea_build_plan(self.root, workspace.IdeaXmlInputs())

    def write(self, value):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(value)

    def test_new_workspace_plan_is_read_only_and_creates_native_root_link(self):
        plan = self.plan()
        self.assertIsNone(plan['before'])
        self.assertFalse(self.path.exists())
        tree = workspace.parse_idea_xml_bytes(plan['after'])
        settings = tree.getroot().find("./component[@name='GradleSettings']/option[@name='linkedExternalProjectsSettings']/set/GradleProjectSettings")
        self.assertEqual(settings.find("option[@name='externalProjectPath']").get('value'), '$PROJECT_DIR$')
        self.assertEqual(settings.find("option[@name='delegatedBuild']").get('value'), 'false')
        self.assertIsNone(settings.find("option[@name='testRunner']"))
        workspace.assert_idea_build_plan_current(plan)
        workspace.write_idea_xml(plan['path'], plan['after'])
        self.assertEqual(self.plan()['after'], self.path.read_bytes())

    def test_only_root_delegation_changes_and_test_runner_unknown_links_survive(self):
        self.write('''<project version="4"><component name="GradleSettings"><option name="customGlobal" value="keep" />
<option name="linkedExternalProjectsSettings"><set>
<GradleProjectSettings keep="root"><option name="externalProjectPath" value="$PROJECT_DIR$" />
<option name="delegatedBuild" value="true" /><option name="testRunner" value="GRADLE" />
<option name="resolveModulePerSourceSet" value="false" /><option name="customProject" value="keep" /></GradleProjectSettings>
<GradleProjectSettings keep="other"><option name="externalProjectPath" value="/unrelated/project" />
<option name="delegatedBuild" value="true" /><option name="testRunner" value="PLATFORM" /></GradleProjectSettings>
</set></option></component><component name="Other" keep="yes" /></project>''')
        original = self.path.read_bytes()
        plan = self.plan()
        self.assertEqual(self.path.read_bytes(), original)
        tree = workspace.parse_idea_xml_bytes(plan['after'])
        root = tree.getroot().find(".//GradleProjectSettings[@keep='root']")
        self.assertEqual(root.find("option[@name='delegatedBuild']").get('value'), 'false')
        self.assertEqual(root.find("option[@name='testRunner']").get('value'), 'GRADLE')
        self.assertEqual(root.find("option[@name='resolveModulePerSourceSet']").get('value'), 'false')
        self.assertEqual(root.find("option[@name='customProject']").get('value'), 'keep')
        other = tree.getroot().find(".//GradleProjectSettings[@keep='other']")
        self.assertEqual(other.find("option[@name='delegatedBuild']").get('value'), 'true')
        self.assertEqual(other.find("option[@name='testRunner']").get('value'), 'PLATFORM')
        self.assertIsNotNone(tree.getroot().find("component[@name='Other'][@keep='yes']"))

    def test_absolute_root_is_matched_but_duplicate_links_fail_without_writing(self):
        self.write(f'''<project><component name="GradleSettings"><option name="linkedExternalProjectsSettings"><set>
<GradleProjectSettings><option name="externalProjectPath" value="{self.root}" /></GradleProjectSettings>
</set></option></component></project>''')
        plan = self.plan()
        tree = workspace.parse_idea_xml_bytes(plan['after'])
        self.assertEqual(len(tree.findall('.//GradleProjectSettings')), 1)
        self.assertEqual(tree.find(".//option[@name='delegatedBuild']").get('value'), 'false')
        self.path.write_text(self.path.read_text().replace('</set>', '<GradleProjectSettings><option name="externalProjectPath" value="$PROJECT_DIR$" /></GradleProjectSettings></set>'))
        before = self.path.read_bytes()
        with self.assertRaises(workspace.WorkspaceError):
            self.plan()
        self.assertEqual(self.path.read_bytes(), before)

    def test_cas_preserves_concurrent_file_and_rejects_symlink(self):
        plan = self.plan()
        self.write('<project><component name="Concurrent" /></project>')
        current = self.path.read_bytes()
        with self.assertRaises(workspace.WorkspaceError):
            workspace.assert_idea_build_plan_current(plan)
        self.assertEqual(self.path.read_bytes(), current)
        plan = self.plan()
        self.path.write_text('<project><component name="ChangedAgain" /></project>')
        with self.assertRaises(workspace.WorkspaceError):
            workspace.assert_idea_build_plan_current(plan)
        target = self.root / 'untouched.xml'
        target.write_text('<project />')
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(workspace.WorkspaceError):
            self.plan()
        self.assertEqual(target.read_text(), '<project />')

    def test_invalid_or_ambiguous_metadata_is_preserved(self):
        for value in ('<!DOCTYPE project [<!ENTITY x "private">]><project>&x;</project>',
                      '<project><component name="GradleSettings"/><component name="GradleSettings"/></project>',
                      '<project><component name="GradleSettings"><option name="linkedExternalProjectsSettings"><list/></option></component></project>'):
            self.write(value)
            with self.assertRaises(workspace.WorkspaceError):
                self.plan()
            self.assertEqual(self.path.read_text(), value)


if __name__ == '__main__':
    unittest.main()
