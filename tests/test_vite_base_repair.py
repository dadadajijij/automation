import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import runner
from subpath.adapters import apply_framework_config_adapters
from subpath.models import FrontendProjectStrategy, SubpathPlan
from subpath.runtime_audit import runtime_subpath_findings_for_html
from subpath.vite_base import direct_config_base, rewrite_vite_base


class ViteBaseRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "vite.config.js"
        self.original = 'export default defineConfig({\n  base: process.env.VITE_BASE_PATH || "./",\n  build: { outDir: "dist" },\n});\n'
        self.config.write_text(self.original)
        self.package = {"scripts": {"build": "vite build"}}
        self.write_package()
        self.docker = self.root / "Dockerfile"
        self.docker.write_text('FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nRUN npm run build\nCMD ["node", "server.cjs"]\n')
        self.project = FrontendProjectStrategy(
            project_id="root", framework="vite", proxy_mode="preserve_prefix",
            adapter="vite", project_root=self.root, source_roots=(),
            runtime_roots=(self.root / "dist",), config_files=(self.config,),
            capabilities=("config_adapter",), evidence=("declaration",),
        )

    def write_package(self):
        (self.root / "package.json").write_text(json.dumps(self.package))

    def apply(self, projects=None):
        report = []
        changes = apply_framework_config_adapters(
            SubpathPlan(projects=tuple(projects or (self.project,)), default_project="root"),
            "example", parse_package_json=runner.parse_package_json,
            read_text=runner.read_text, write_text=runner.write_text,
            deployment_root=self.root, adaptation_report=report,
        )
        return changes, report

    def test_repair_active_root_build_and_second_run_is_idempotent(self):
        changes, report = self.apply()
        self.assertTrue(changes)
        self.assertIn('base: "/tools2/example/"', self.config.read_text())
        self.assertEqual(report[0]["action"], "repaired_relative_base")
        self.assertEqual(report[0]["build_evidence"], "confirmed_root_build")
        changes, report = self.apply()
        self.assertEqual(changes, [])
        self.assertEqual(report[0]["action"], "already_prefixed")

    def test_strip_prefix_keeps_relative_base(self):
        self.project = replace(self.project, proxy_mode="strip_prefix")
        changes, report = self.apply()
        self.assertEqual(changes, [])
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(report[0]["build_evidence"], "preserved_proxy_mode")

    def test_inactive_config_and_cli_base_are_preserved(self):
        for script, reason in (
            ("vite build --config other.js", "different_build_config"),
            ("vite build --base=/custom/", "cli_base_override"),
            ("vite build && node patch-output.js", "build_not_confirmed"),
            ("node custom-build.js", "build_not_confirmed"),
            ("vite build different-root", "build_not_confirmed"),
            ("vite build -cother.js", "build_not_confirmed"),
        ):
            with self.subTest(script=script):
                self.package["scripts"]["build"] = script
                self.write_package()
                changes, report = self.apply()
                self.assertEqual(changes, [])
                self.assertEqual(report[0]["build_evidence"], reason)

    def test_declared_custom_config_must_match_build_command(self):
        custom = self.config.with_name("custom.vite.js")
        self.config.rename(custom)
        self.config = custom
        self.project = replace(self.project, config_files=(custom,))
        self.assertEqual(self.apply()[0], [])
        self.package["scripts"]["build"] = "vite build --config custom.vite.js"
        self.write_package()
        self.assertTrue(self.apply()[0])

    def test_docker_without_build_or_with_explicit_env_is_preserved(self):
        original = self.docker.read_text()
        for docker, reason in (
            (original.replace("RUN npm run build\n", ""), "build_not_confirmed"),
            (original.replace("RUN npm run build", "ENV VITE_BASE_PATH=/cdn/\nRUN npm run build"), "build_env_override"),
            (original.replace("RUN npm run build", "RUN node custom.js\nRUN npm run build"), "build_not_confirmed"),
            (original + "FROM nginx:alpine\n", "build_not_confirmed"),
        ):
            with self.subTest(reason=reason):
                self.docker.write_text(docker)
                changes, report = self.apply()
                self.assertEqual(changes, [])
                self.assertEqual(report[0]["build_evidence"], reason)

    def test_nested_entry_and_hooks_are_preserved(self):
        self.project = replace(self.project, runtime_entry_hint="/nested/")
        self.assertEqual(self.apply()[1][0]["build_evidence"], "entry_path_not_confirmed")
        self.project = replace(self.project, runtime_entry_hint=None)
        self.package["scripts"]["postbuild"] = "node replace-html.js"
        self.write_package()
        self.assertEqual(self.apply()[0], [])

    def test_shared_configuration_is_processed_once(self):
        changes, report = self.apply((self.project, replace(self.project, project_id="duplicate")))
        self.assertEqual(len(changes), 1)
        self.assertEqual(len(report), 1)

    def test_custom_paths_dynamic_configs_and_other_frameworks_are_preserved(self):
        for text in (
            'export default defineConfig({ base: "/custom/" });',
            'export default defineConfig({ base: "https://cdn.example/" });',
            'export default defineConfig(() => ({ base: "./" }));',
            'export default defineConfig({ base: determineBase() });',
        ):
            with self.subTest(text=text):
                self.config.write_text(text)
                self.assertEqual(self.apply()[0], [])
                self.assertEqual(self.config.read_text(), text)
        self.config.write_text(self.original)
        self.project = replace(self.project, framework="generic", adapter="static_rewrite")
        self.assertEqual(self.apply(), ([], []))

    def test_workspace_with_correct_base_stays_unchanged(self):
        frontend = self.root / "frontend"
        frontend.mkdir()
        config = frontend / "vite.config.ts"
        text = 'export default defineConfig({ base: "/tools2/example/" });'
        config.write_text(text)
        self.project = replace(self.project, project_root=frontend, config_files=(config,))
        self.assertEqual(self.apply()[0], [])
        self.assertEqual(config.read_text(), text)

    def test_safe_property_edit_ignores_comments_strings_and_nested_base(self):
        text = '''const label = "base: './'";
// export default defineConfig({base: './'})
export default defineConfig({
  build: { base: './', outDir: 'dist' },
  /* base: '/wrong/' */ base /* comment */: process.env.ASSET_BASE ?? './',
});'''
        rewritten, reason = rewrite_vite_base(text, "/tools2/example/", repair_relative=True)
        self.assertEqual(reason, "repaired_relative_base")
        self.assertIn("build: { base: './'", rewritten)
        self.assertIn('base /* comment */: "/tools2/example/"', rewritten)
        self.assertIn('const label = "base:', rewritten)

    def test_ambiguous_objects_are_not_rewritten(self):
        for text in (
            'export default defineConfig({ base: "./", ...overrides });',
            'export default defineConfig({ ...defaults, base: "./" });',
            'export default defineConfig({ base: "./", base: "/custom/" });',
            'export default defineConfig({ base: "./", [key]: value });',
            'export default defineConfig({ base: "./" }, otherOptions);',
            'export default defineConfig({ base: "./" }); mutateConfig();',
            'export default defineConfig({ base: "./", test: /[{},]/ });',
            'export default defineConfig({ base: `${prefix}/` });',
        ):
            with self.subTest(text=text):
                self.assertIsNone(direct_config_base(text))
                self.assertEqual(rewrite_vite_base(text, "/tools2/example/", repair_relative=True)[0], text)

    def test_quoted_or_commented_property_does_not_gain_duplicate_base(self):
        for prop in ('"base": "./"', "'base': './'", 'base /* comment */: "./"'):
            with self.subTest(property=prop):
                text = f"export default defineConfig({{\n  {prop},\n}});"
                self.assertEqual(rewrite_vite_base(text, "/tools2/example/")[0], text)
                rewritten, reason = rewrite_vite_base(text, "/tools2/example/", repair_relative=True)
                self.assertEqual(reason, "repaired_relative_base")
                self.assertEqual(rewritten.count("/tools2/example/"), 1)
                self.assertNotIn('"./"', rewritten)

    def test_original_failure_and_expected_asset_urls_at_both_entries(self):
        base = "/tools2/example"
        html = '<script src="./assets/app.js"></script><link href="./assets/app.css">'
        findings = runtime_subpath_findings_for_html(html, base, base)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0]["resolved_url"], "/tools2/assets/app.js")
        self.assertIn("resolves to", findings[0]["message"])
        self.apply()
        # Model Vite's base substitution here; the separate isolated build
        # validation checks actual generated assets using the historical image.
        fixed_html = html.replace("./assets/", "/tools2/example/assets/")
        for entry in (base, base + "/"):
            self.assertEqual(runtime_subpath_findings_for_html(fixed_html, entry, base), [])


if __name__ == "__main__":
    unittest.main()
