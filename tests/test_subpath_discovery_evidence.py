import json
import tempfile
import unittest
from pathlib import Path

import runner
from subpath.discovery import classify_runtime_root_groups, classify_runtime_roots, detect_runtime_root_evidence


class SubpathDiscoveryEvidenceTests(unittest.TestCase):
    def test_detect_runtime_root_evidence_marks_node_static_root_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            scripts_dir = repo_dir / "scripts"
            demo_dir = repo_dir / "Demo"
            scripts_dir.mkdir(parents=True)
            demo_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"scripts": {"dev": "node ./scripts/server.js"}}) + "\n",
                encoding="utf-8",
            )
            (scripts_dir / "server.js").write_text(
                '\n'.join([
                    'const express = require("express");',
                    'const path = require("path");',
                    'const dir = path.join(__dirname, "../Demo");',
                    'const app = express();',
                    'app.use(express.static(dir));',
                ]),
                encoding="utf-8",
            )

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            node_evidence = [item for item in evidence if item.source == "node_static_call"]
            self.assertTrue(node_evidence)
            self.assertEqual(node_evidence[0].confidence, "high")
            self.assertEqual(node_evidence[0].path, demo_dir.resolve())

    def test_detect_runtime_root_evidence_marks_workspace_anchor_medium_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            frontend_src_dir = repo_dir / "frontend" / "src"
            frontend_src_dir.mkdir(parents=True)
            (repo_dir / "package.json").write_text(
                json.dumps({"name": "demo-workspaces", "private": True, "workspaces": ["frontend"]}) + "\n",
                encoding="utf-8",
            )
            (repo_dir / "frontend" / "package.json").write_text(
                json.dumps({"name": "frontend", "private": True, "dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}) + "\n",
                encoding="utf-8",
            )

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            workspace_evidence = [item for item in evidence if item.source == "workspace_anchor"]
            self.assertTrue(workspace_evidence)
            self.assertTrue(any(item.path == frontend_src_dir.resolve() and item.confidence == "medium" for item in workspace_evidence))

    def test_classify_runtime_roots_splits_confirmed_and_hinted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            confirmed_root = (root / "confirmed").resolve()
            hinted_root = (root / "hinted").resolve()
            confirmed_root.mkdir()
            hinted_root.mkdir()

            from subpath.models import RuntimeRootEvidence

            confirmed, hinted = classify_runtime_roots(
                [
                    RuntimeRootEvidence(confirmed_root, "node_static_call", "high", "node"),
                    RuntimeRootEvidence(hinted_root, "directory_hint", "low", "hint"),
                ]
            )

            self.assertEqual(confirmed, (confirmed_root,))
            self.assertEqual(hinted, (hinted_root,))

    def test_classify_runtime_root_groups_separates_service_and_page_cluster_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service_root = (root / "Demo").resolve()
            cluster_root = (root / "Demo" / "basicVoiceCall").resolve()
            service_root.mkdir(parents=True)
            cluster_root.mkdir(parents=True)

            from subpath.models import RuntimeRootEvidence

            groups = classify_runtime_root_groups(
                [
                    RuntimeRootEvidence(service_root, "node_static_call", "high", "node"),
                    RuntimeRootEvidence(cluster_root, "html_cluster", "medium", "cluster"),
                ]
            )

            self.assertEqual(groups["service_roots"], (service_root,))
            self.assertEqual(groups["page_cluster_roots"], (cluster_root,))
            self.assertEqual(groups["hinted_roots"], ())

    def test_detect_runtime_root_evidence_marks_html_cluster_medium_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            cluster_dir = repo_dir / "Demo" / "basicLive"
            cluster_dir.mkdir(parents=True)
            (cluster_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (cluster_dir / "basicLive.js").write_text("console.log('demo')\n", encoding="utf-8")
            (cluster_dir / "index.css").write_text("body{}\n", encoding="utf-8")
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            cluster_evidence = [item for item in evidence if item.source == "html_cluster"]
            self.assertTrue(cluster_evidence)
            self.assertTrue(any(item.path == cluster_dir.resolve() and item.confidence == "medium" for item in cluster_evidence))

    def test_detect_runtime_root_evidence_marks_multi_page_html_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            cluster_dir = repo_dir / "examples" / "gallery"
            cluster_dir.mkdir(parents=True)
            (cluster_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (cluster_dir / "detail.html").write_text("<html></html>\n", encoding="utf-8")
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            cluster_evidence = [item for item in evidence if item.source == "html_cluster" and item.path == cluster_dir.resolve()]
            self.assertTrue(cluster_evidence)
            self.assertIn("multiple_html_pages", cluster_evidence[0].detail)

    def test_detect_runtime_root_evidence_marks_index_with_nested_pages_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            cluster_dir = repo_dir / "Demo"
            nested_dir = cluster_dir / "basicLive"
            nested_dir.mkdir(parents=True)
            (cluster_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (nested_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            cluster_evidence = [item for item in evidence if item.source == "html_cluster" and item.path == cluster_dir.resolve()]
            self.assertTrue(cluster_evidence)
            self.assertIn("index_html_with_nested_pages", cluster_evidence[0].detail)

    def test_detect_runtime_root_evidence_marks_html_cluster_with_shared_assets_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            cluster_dir = repo_dir / "site"
            assets_dir = cluster_dir / "assets"
            assets_dir.mkdir(parents=True)
            (cluster_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (assets_dir / "app.js").write_text("console.log('demo')\n", encoding="utf-8")
            (repo_dir / "package.json").write_text(json.dumps({"name": "demo"}) + "\n", encoding="utf-8")

            evidence = detect_runtime_root_evidence(
                repo_dir,
                parse_package_json=runner.parse_package_json,
                detect_node_entry_script_paths=runner.detect_node_entry_script_paths,
                read_text_if_exists=runner.read_text_if_exists,
                collect_python_frontend_hint_files_fn=runner.collect_python_frontend_hint_files,
                workspace_frontend_package_dirs_fn=runner.workspace_frontend_package_dirs,
                collect_matching_files=runner.collect_matching_files,
            )

            cluster_evidence = [item for item in evidence if item.source == "html_cluster" and item.path == cluster_dir.resolve()]
            self.assertTrue(cluster_evidence)
            self.assertIn("shared_asset_dirs=assets", cluster_evidence[0].detail)


if __name__ == "__main__":
    unittest.main()
