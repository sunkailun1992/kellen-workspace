"""原生 IDEA External Tool 的独立归属、CAS 与真实平台 XML 合同。"""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ExternalToolPlanTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tools = importlib.import_module("idea_external_tools")
        except ModuleNotFoundError:
            self.fail("native external tool planner is not implemented")
        self.temporary = tempfile.TemporaryDirectory(prefix="IDE 原生启动 ")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "personal workspace"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts/prepare-observed.py").write_text("# synthetic preflight fixture\n")
        self.config = self.base / "IDE-config"
        self.config.mkdir()

    def test_plan_is_read_only_and_uses_nine_native_quiet_preflights(self):
        import workspace
        plan = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        self.assertFalse(plan["path"].exists())
        tree = workspace.parse_idea_xml_bytes(plan["after"])
        self.assertEqual(tree.getroot().tag, "toolSet")
        self.assertEqual(tree.getroot().get("name"), "KellenWorkspaceObservability")
        entries = tree.getroot().findall("tool")
        self.assertEqual(len(entries), 9)
        for tool in entries:
            options = {v.get("name"): v.get("value") for v in tool.findall("./exec/option")}
            self.assertNotIn("gradle", options["PARAMETERS"])
            self.assertIn("--check-idea --quiet", options["PARAMETERS"])
            self.assertEqual(options["WORKING_DIRECTORY"], str(self.root.resolve()))
            self.assertIn(plan["root_id"], tool.get("description"))
        self.assertEqual(self.tools.tool_action_id("rag"), "Tool_KellenWorkspaceObservability_Prepare rag")

    def test_apply_only_changes_owned_group_and_retains_owner_through_ide_save(self):
        import workspace
        directory = self.config / "tools"
        directory.mkdir()
        unrelated = directory / "Existing-user-tools.xml"
        unrelated.write_text("<toolSet name='Existing-user-tools'/>\n")
        original = unrelated.read_bytes()
        plan = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        result = self.tools.apply_external_tools(plan, self.base / "backup")
        self.assertTrue(result["changed"])
        self.assertFalse(result["ui_loaded"])
        self.assertEqual(plan["path"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(unrelated.read_bytes(), original)
        # IDEA 的 scheme writer 会重排缩进和属性；ownership 不依赖会被丢弃的注释。
        tree = workspace.parse_idea_xml_bytes(plan["path"].read_bytes())
        rewritten = workspace.idea_xml_bytes(tree).replace(b"  <tool", b"    <tool")
        plan["path"].write_bytes(rewritten)
        updated = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        result = self.tools.apply_external_tools(updated, self.base / "backup")
        self.assertEqual(Path(result["backup"]).read_bytes(), rewritten)
        self.assertEqual(Path(result["backup"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(unrelated.read_bytes(), original)

    def test_unknown_file_different_root_and_changed_commands_are_preserved(self):
        import workspace
        plan = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        plan["path"].parent.mkdir()
        cases = [b"<toolSet name='user-owned'/>"]
        other = self.base / "another-root"
        (other / "scripts").mkdir(parents=True)
        (other / "scripts/prepare-observed.py").write_text("# synthetic\n")
        cases.append(self.tools.plan_external_tools(other, self.config, sys.executable)["after"])
        tree = workspace.parse_idea_xml_bytes(plan["after"])
        tree.getroot().find("./tool/exec/option[@name='COMMAND']").set("value", "/unknown/program")
        cases.append(workspace.idea_xml_bytes(tree))
        tree = workspace.parse_idea_xml_bytes(plan["after"])
        tree.getroot().find("tool").set("showInEditor", "true")
        cases.append(workspace.idea_xml_bytes(tree))
        tree = workspace.parse_idea_xml_bytes(plan["after"])
        from xml.etree.ElementTree import SubElement
        SubElement(tree.getroot().find("tool"), "filter", {"name": "user-filter"})
        cases.append(workspace.idea_xml_bytes(tree))
        for data in cases:
            with self.subTest(data_prefix=data[:40]):
                plan["path"].write_bytes(data)
                with self.assertRaises(ValueError):
                    self.tools.plan_external_tools(self.root, self.config, sys.executable)
                self.assertEqual(plan["path"].read_bytes(), data)

    def test_cas_and_symlinks_do_not_overwrite_concurrent_or_foreign_files(self):
        plan = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        plan["path"].parent.mkdir()
        plan["path"].write_text("user wrote while plan pending")
        with self.assertRaises(ValueError):
            self.tools.apply_external_tools(plan, self.base / "backup")
        self.assertEqual(plan["path"].read_text(), "user wrote while plan pending")
        plan["path"].unlink()
        victim = self.base / "keep.xml"
        victim.write_text("do not change")
        plan["path"].symlink_to(victim)
        with self.assertRaises(ValueError):
            self.tools.plan_external_tools(self.root, self.config, sys.executable)
        self.assertEqual(victim.read_text(), "do not change")

    def test_python_stable_symlink_is_kept_and_invalid_runtime_is_rejected(self):
        program = self.base / "python3"
        program.symlink_to(sys.executable)
        plan = self.tools.plan_external_tools(self.root, self.config, program)
        self.assertEqual(plan["python"], program)
        self.assertIn(str(program).encode(), plan["after"])
        program.unlink()
        program.write_text("#!/bin/sh\nprintf '3.9'\n")
        program.chmod(0o700)
        with self.assertRaises(ValueError):
            self.tools.plan_external_tools(self.root, self.config, program)

    @unittest.skipUnless(sys.platform == "darwin", "installed IDEA native schema fixture is macOS-only")
    def test_installed_idea_reads_native_tool_attributes_and_before_run_schema(self):
        app = Path("/Applications/IntelliJ IDEA.app/Contents")
        java = app / "jbr/Contents/Home/bin/java"
        library = app / "lib/intellij.platform.execution.impl.jar"
        if not java.is_file() or not library.is_file() or not shutil.which("javac"):
            self.skipTest("installed IDEA/JBR and javac are not available")
        plan = self.tools.plan_external_tools(self.root, self.config, sys.executable)
        fixture = self.base / "tools.xml"
        fixture.write_bytes(plan["after"])
        source = self.base / "NativeSchema.java"
        source.write_text('''import java.lang.reflect.*;
import java.util.*;
public class NativeSchema {
 public static void main(String[] args) throws Exception {
  Class<?> el=Class.forName("org.jdom.Element");
  Class<?> builder=Class.forName("org.jdom.input.SAXBuilder");
  Object b=builder.getConstructor().newInstance();
  builder.getMethod("setFeature",String.class,boolean.class).invoke(b,"http://apache.org/xml/features/disallow-doctype-decl",true);
  Object document=builder.getMethod("build",java.io.File.class).invoke(b,new java.io.File(args[0]));
  Object root=document.getClass().getMethod("getRootElement").invoke(document);
  Class<?> tool=Class.forName("com.intellij.tools.Tool");
  Class<?> processor=Class.forName("com.intellij.tools.ToolManager$createProcessor$1");
  Constructor<?> pc=processor.getDeclaredConstructor();pc.setAccessible(true);Object p=pc.newInstance();
  Method read=Class.forName("com.intellij.tools.ToolsProcessor").getDeclaredMethod("readToolAttributes",el,tool);read.setAccessible(true);
  Method save=Class.forName("com.intellij.tools.ToolsProcessor").getDeclaredMethod("saveToolAttributes",tool,el);save.setAccessible(true);
  Class<?> task=Class.forName("com.intellij.tools.ToolBeforeRunTask");
  Constructor<?> tc=task.getDeclaredConstructor();tc.setAccessible(true);
  Method attr=el.getMethod("setAttribute",String.class,String.class);
  int count=0;
  for(Object entry:(List<?>)el.getMethod("getChildren",String.class).invoke(root,"tool")) {
   Object v=tool.getConstructor().newInstance();read.invoke(p,entry,v);
   tool.getMethod("setGroup",String.class).invoke(v,"KellenWorkspaceObservability");
   String name=(String)tool.getMethod("getName").invoke(v);
   String id=(String)tool.getMethod("getActionId").invoke(v);
   if(!id.equals("Tool_KellenWorkspaceObservability_"+name))throw new AssertionError("native action id mismatch");
   if(!(Boolean)tool.getMethod("isEnabled").invoke(v))throw new AssertionError("tool disabled");
   if(!(Boolean)tool.getMethod("isUseConsole").invoke(v))throw new AssertionError("missing error console");
   Object attributes=el.getConstructor(String.class).newInstance("tool");save.invoke(p,v,attributes);
   for(String key:new String[]{"showInMainMenu","showInEditor","showInProject","showInSearchPopup","disabled","useConsole","showConsoleOnStdOut","showConsoleOnStdErr","synchronizeAfterRun"}) {
    Object original=el.getMethod("getAttributeValue",String.class).invoke(entry,key);
    Object saved=el.getMethod("getAttributeValue",String.class).invoke(attributes,key);
    if(!Objects.equals(original,saved))throw new AssertionError("native flag roundtrip failed: "+key);
   }
   Object hook=el.getConstructor(String.class).newInstance("option");
   attr.invoke(hook,"name","ToolBeforeRunTask");attr.invoke(hook,"enabled","true");attr.invoke(hook,"actionId",id);
   Object t=tc.newInstance();task.getMethod("readExternal",el).invoke(t,hook);
   Object out=el.getConstructor(String.class).newInstance("option");task.getMethod("writeExternal",el).invoke(t,out);
   if(!id.equals(el.getMethod("getAttributeValue",String.class).invoke(out,"actionId")))throw new AssertionError("before run roundtrip failed");
   count++;
  }
  if(count!=9)throw new AssertionError("tool count mismatch");
  System.out.println("NATIVE_TOOL_ATTRIBUTES_AND_BEFORE_RUN_ROUNDTRIP_OK 9");
 }
}''')
        compiled = subprocess.run([shutil.which("javac"), str(source)], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        classpath = os.pathsep.join([str(self.base), *(str(p) for p in (app / "lib").glob("*.jar"))])
        result = subprocess.run([str(java), "-cp", classpath, "NativeSchema", str(fixture)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NATIVE_TOOL_ATTRIBUTES_AND_BEFORE_RUN_ROUNDTRIP_OK 9", result.stdout)


if __name__ == "__main__":
    unittest.main()
