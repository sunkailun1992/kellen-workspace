"""Gradle fixture checks run with WORKSPACE_GRADLE_TESTS=1 (no network dependencies)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "repos.json").read_text())
JAVA = [r for r in MANIFEST["repositories"] if r["kind"].startswith("java-")]
SERVICES = [r for r in JAVA if r["kind"] == "java-service"]


class RunConfigurationTests(unittest.TestCase):
    def test_each_service_runs_its_included_build_from_workspace(self):
        files = list((ROOT / ".run").glob("*.run.xml"))
        self.assertEqual(len(files), len(SERVICES))
        actual = {}
        for path in files:
            config = ET.parse(path).getroot().find("configuration")
            self.assertEqual(config.attrib["type"], "GradleRunConfiguration")
            settings = config.find("ExternalSystemSettings")
            options = {e.attrib["name"]: e for e in settings.findall("option")}
            self.assertEqual(options["externalProjectPath"].attrib["value"], "$PROJECT_DIR$")
            self.assertEqual(options["externalSystemIdString"].attrib["value"], "GRADLE")
            tasks = options["taskNames"].findall("./list/option")
            self.assertEqual(len(tasks), 1)
            actual[tasks[0].attrib["value"]] = path.name
            self.assertIsNone(options.get("env"))
        self.assertEqual(set(actual), {f':{r["name"]}:bootRun' for r in SERVICES})


@unittest.skipUnless(os.environ.get("WORKSPACE_GRADLE_TESTS") == "1", "set WORKSPACE_GRADLE_TESTS=1 for Gradle integration fixtures")
class CompositeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workspace 空格 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "工作区"
        self.root.mkdir()
        for name in ("settings.gradle", "build.gradle", "gradle.properties", "repos.json", "gradlew", "gradlew.bat"):
            shutil.copy2(ROOT / name, self.root / name)
        shutil.copytree(ROOT / "gradle", self.root / "gradle")
        shutil.copytree(ROOT / ".run", self.root / ".run")
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/verify-composite.gradle", self.root / "scripts/verify-composite.gradle")

    def gradle(self, *args):
        return subprocess.run([str(self.root / "gradlew"), "--offline", "--console=plain", "--no-configuration-cache", *args],
                              cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)

    def create_builds(self):
        for repo in JAVA:
            path = self.root / repo["name"]
            path.mkdir()
            (path / "settings.gradle").write_text(f"rootProject.name = '{repo['name']}'\n")
            (path / "build.gradle").write_text("plugins { id 'java-library' }\ngroup = 'com'\nversion = '1.0'\n")
            source = path / "src/main/java/fixture"
            source.mkdir(parents=True)
            if repo["kind"] == "java-library":
                cls = "Utils" if repo["name"] == "utils" else "RpcApi"
                (source / f"{cls}.java").write_text(f"package fixture; public class {cls} {{}}\n")
            else:
                (source / "App.java").write_text("package fixture; public class App { Utils u; RpcApi r; }\n")
                with (path / "build.gradle").open("a") as f:
                    f.write(f"repositories {{ maven {{ url = uri('{(self.root / 'fixture-maven').as_uri()}') }} }}\n")
                    f.write("""
dependencies {
    implementation 'com:utils:999.0'
    implementation 'com:rpc-api:999.0'
    implementation 'com.kellen:utils:1.0'
    implementation 'com.kellen:rpc-api:1.0'
}
tasks.register('bootRun', JavaExec) { mainClass = 'fixture.App' }
tasks.register('assertWorkspace') {
    doLast {
        assert tasks.named('bootRun').get().workingDir.canonicalFile == projectDir.canonicalFile
        def dependencies = configurations.compileClasspath.incoming.resolutionResult.allDependencies
        ['utils', 'rpc-api'].each { artifact ->
            def personal = dependencies.find { it.requested.displayName == "com:${artifact}:999.0" }
            def company = dependencies.find { it.requested.displayName == "com.kellen:${artifact}:1.0" }
            assert personal.selected.id instanceof org.gradle.api.artifacts.component.ProjectComponentIdentifier
            assert company.selected.id instanceof org.gradle.api.artifacts.component.ModuleComponentIdentifier
            assert company.selected.moduleVersion.group == 'com.kellen'
        }
        println "VERIFIED ${project.name}: source SDKs, company modules, project workingDir"
    }
}
""")
        for artifact in ("utils", "rpc-api"):
            version = self.root / "fixture-maven/com/kellen" / artifact / "1.0"
            version.mkdir(parents=True)
            with zipfile.ZipFile(version / f"{artifact}-1.0.jar", "w"):
                pass
            (version / f"{artifact}-1.0.pom").write_text(f"<project><modelVersion>4.0.0</modelVersion><groupId>com.kellen</groupId><artifactId>{artifact}</artifactId><version>1.0</version></project>")

    def test_missing_repository_has_bootstrap_instruction(self):
        result = self.gradle("verifyWorkspace")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("./scripts/bootstrap.sh", result.stdout)
        self.assertIn("utils", result.stdout)
        self.assertNotIn("BUILD SUCCESSFUL", result.stdout)

    def test_all_java_builds_compile_and_only_personal_sdks_substitute(self):
        self.create_builds()
        result = self.gradle("-I", "scripts/verify-composite.gradle", "verifyWorkspace", "compileAllJava",
                             *[task for r in SERVICES for task in (f":{r['name']}:assertWorkspace", f":{r['name']}:verifyCompositeModel")])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("11 Java", result.stdout)
        for repo in SERVICES:
            self.assertIn(f"VERIFIED {repo['name']}:", result.stdout)
            self.assertIn(f"{repo['name']}: bootRun workingDir=projectDir", result.stdout)
            self.assertIn(f"{repo['name']}: verified 2 personal SDK source dependencies, 2 company module dependencies", result.stdout)
        for repo in JAVA:
            self.assertTrue((self.root / repo["name"] / "build/classes/java/main/fixture").is_dir())

    def test_symlinked_build_uses_target_working_directory(self):
        self.create_builds()
        original = self.root / "gateway"
        target = Path(self.temp.name) / "复用 service"
        original.rename(target)
        original.symlink_to(target, target_is_directory=True)
        with (self.root / 'build.gradle').open('a') as f:
            f.write("""
tasks.register('assertIncludedBuildIdentity') {
    doLast {
        def included = gradle.includedBuild('gateway')
        assert included.projectDir.absolutePath == included.projectDir.canonicalPath
        println 'VERIFIED included build uses physical path identity'
    }
}
""")
        result = self.gradle("-I", "scripts/verify-composite.gradle", "assertIncludedBuildIdentity", ":gateway:assertWorkspace", ":gateway:verifyCompositeModel")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("VERIFIED gateway:", result.stdout)
        self.assertIn('VERIFIED included build uses physical path identity', result.stdout)

    def test_idea_model_excludes_legacy_mobile_but_preserves_directory(self):
        self.create_builds()
        target = Path(self.temp.name) / '独立 mobile target'
        target.mkdir()
        marker = target / 'keep.txt'
        marker.write_bytes(b'independent mobile stays untouched\n')
        mobile = self.root / 'ai-wechat-mini-programs'
        mobile.symlink_to(target, target_is_directory=True)
        with (self.root / 'build.gradle').open('a') as f:
            f.write("""
tasks.register('assertIdeaScope') {
    doLast {
        def excluded = idea.module.excludeDirs
        def legacy = file('ai-wechat-mini-programs')
        assert excluded.contains(legacy)
        assert excluded.contains(legacy.canonicalFile)
        assert !excluded.contains(file('admin-web'))
        assert gradle.includedBuilds.size() == 11
        assert !gradle.includedBuilds.any { it.name == 'ai-wechat-mini-programs' }
    }
}
""")
        result = self.gradle('assertIdeaScope', 'ideaModule')
        self.assertEqual(result.returncode, 0, result.stdout)
        module = ET.parse(self.root / 'kellen-workspace.iml')
        excluded_urls = {item.get('url') for item in module.findall('.//excludeFolder')}
        excluded_paths = {Path(url.removeprefix('file://').replace('$MODULE_DIR$', str(self.root))).resolve()
                          for url in excluded_urls}
        self.assertIn(target.resolve(), excluded_paths)
        self.assertNotIn((self.root / 'admin-web').resolve(), excluded_paths)
        self.assertTrue(mobile.is_symlink())
        self.assertEqual(marker.read_bytes(), b'independent mobile stays untouched\n')

    def test_idea_model_preserves_active_web_when_legacy_mobile_aliases_it(self):
        self.create_builds()
        web = self.root / 'admin-web'
        web.mkdir()
        marker = web / 'keep.txt'
        marker.write_bytes(b'active web stays untouched\n')
        mobile = self.root / 'ai-wechat-mini-programs'
        mobile.symlink_to(web, target_is_directory=True)
        with (self.root / 'build.gradle').open('a') as f:
            f.write("""
tasks.register('assertActiveWebScope') {
    doLast {
        def web = file('admin-web').canonicalFile
        assert !idea.module.excludeDirs.any { it.canonicalFile == web }
        assert gradle.includedBuilds.size() == 11
    }
}
""")
        result = self.gradle('assertActiveWebScope', 'ideaModule')
        self.assertEqual(result.returncode, 0, result.stdout)
        module = ET.parse(self.root / 'kellen-workspace.iml')
        excluded = {Path(item.get('url').removeprefix('file://').replace('$MODULE_DIR$', str(self.root))).resolve()
                    for item in module.findall('.//excludeFolder')}
        self.assertNotIn(web.resolve(), excluded)
        self.assertTrue(mobile.is_symlink())
        self.assertEqual(marker.read_bytes(), b'active web stays untouched\n')


if __name__ == "__main__":
    unittest.main()
