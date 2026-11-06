import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xdf_classifier", ROOT / "scripts" / "classify_issue.py")
classifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier)
CASES = json.loads((ROOT / "tests" / "reported-cases.json").read_text(encoding="utf-8"))["cases"]


class ClassificationTests(unittest.TestCase):
    def test_all_50_customer_reports(self):
        self.assertEqual([c["source_index"] for c in CASES], list(range(1, 51)))
        for case in CASES:
            with self.subTest(source_index=case["source_index"]):
                result = classifier.classify(case["text"])
                self.assertEqual(sorted({c["id"] for c in result["categories"]}), sorted(case["categories"]))
                self.assertEqual(result["status"], case["status"])
                if "fallback_intents" in case:
                    self.assertEqual(sorted(result["fallback"]["intents"]), sorted(case["fallback_intents"]))
                    self.assertTrue(result["fallback"]["required"])

    def test_courseware_priority_and_separate_microphone_symptom(self):
        result = classifier.classify("播放音频进度条不动，没有声音，学生麦克风也没有声音")
        self.assertEqual({c["id"] for c in result["categories"]}, {"courseware-audio-playback", "audio-no-sound"})
        single = classifier.classify("不是麦克风没声音，是课件音频播放不了")
        self.assertEqual({c["id"] for c in single["categories"]}, {"courseware-audio-playback"})

    def test_negation_resolution_and_normal_observation(self):
        for text in ("没有回声，声音正常", "之前有回声，现在已经恢复了", "音频能正常播放，没有卡顿", "学生画面不模糊", "只是咨询回声消除怎么开"):
            with self.subTest(text=text):
                self.assertEqual(classifier.classify(text)["categories"], [])
        result = classifier.classify("没有回声，但是杂音很大")
        self.assertEqual({c["id"] for c in result["categories"]}, {"audio-noise"})

    def test_multiple_subjects_and_media_direction(self):
        text = "老师ID:80000001，学生ID:70000001。老师有回声，学生麦克风没有声音"
        items = classifier.classify(text)["categories"]
        self.assertEqual({c["id"]: c["subject_uid"] for c in items}, {"audio-echo": "80000001", "audio-no-sound": "70000001"})
        direction = classifier.classify("老师ID:80000001，学生ID:70000001。学生听不到老师声音")["categories"][0]
        self.assertEqual(direction["subject_uid"], "70000001")
        self.assertEqual(direction["direction"], {"source_role": "teacher", "receiver_role": "student"})
        self.assertIn("echo_source_uid_not_inferred_from_complainant", items[0]["pending_checks"])

    def test_replay_is_distinct_from_live_and_link_is_only_a_clue(self):
        replay = classifier.classify("回放没有声音，但是老师直播声音正常")
        self.assertEqual(replay["categories"], [])
        self.assertIn("replay-investigation", replay["fallback"]["intents"])
        live = classifier.classify("学生画面模糊，回放链接：https://example.invalid/a.mp4?auth_key=SECRET")
        self.assertEqual([c["id"] for c in live["categories"]], ["video-blur"])
        self.assertNotIn("SECRET", json.dumps(live))
        self.assertNotIn("replay-investigation", live["fallback"]["intents"])

    def test_flash_exit_is_not_network_and_rejoin_is_not_a_complaint(self):
        self.assertEqual(classifier.classify("学生每三分钟闪退，之前调整过接入策略")["categories"], [])
        result = classifier.classify("麦克风没有声音，退出重进没有恢复")
        self.assertEqual({c["id"] for c in result["categories"]}, {"audio-no-sound"})

    def test_identity_aliases_are_strings_not_guessed_uid_or_numeric_channel_id(self):
        text = "课次ID：900000001\n老师：示例老师(80000001)\n学员：示例学员(70000001)\n学员听不到老师"
        identity = classifier.classify(text)["identity"]
        self.assertEqual(identity["channel_names"], ["900000001"])
        self.assertEqual(identity["teacher_uids"], ["80000001"])
        self.assertEqual(identity["student_uids"], ["70000001"])
        account = classifier.classify(CASES[22]["text"])["identity"]
        self.assertEqual(account["student_uids"], [])
        self.assertTrue(account["has_unmapped_student_account"])
        bare = classifier.classify(CASES[16]["text"])["identity"]
        self.assertEqual(bare["channel_names"], [])
        self.assertEqual(bare["unlabeled_channel_candidates"], ["9000017"])
        alternate = classifier.classify(CASES[42]["text"])["identity"]
        self.assertEqual(alternate["channel_names"], ["900000043"])
        self.assertEqual(alternate["student_uids"], ["70000043"])

    def test_platform_is_not_inferred_from_camera_or_customer(self):
        result = classifier.classify("老师摄像头不显示")
        self.assertEqual(result["identity"]["platforms"], [])
        self.assertEqual(result["status"], "ambiguous")
        for text, platform in (("学员鸿蒙设备麦克风没有声音", "HarmonyOS"), ("学生ipad麦克风没声音", "iOS"), ("电脑声音很小", "PC")):
            self.assertEqual(classifier.classify(text)["identity"]["platforms"], [platform])

    def test_specialized_issue_preserves_independent_network_request(self):
        result = classifier.classify("学生麦克风声音很小；另外请查老师网络丢包和设备情况")
        self.assertEqual({c["id"] for c in result["categories"]}, {"audio-low-volume"})
        self.assertTrue(result["fallback"]["required"])
        self.assertEqual(set(result["fallback"]["intents"]), {"rtc-quality", "network-device-check"})
        for evidence in result["fallback"]["evidence"]:
            self.assertEqual(evidence["subject_role"], "teacher")
        duplicate = classifier.classify("课件音频播放进度条卡住")
        self.assertEqual([c["id"] for c in duplicate["categories"]], ["courseware-audio-playback"])
        self.assertFalse(duplicate["fallback"]["required"])
        separate = classifier.classify("音频播放进度条不动，而且学生视频卡顿")
        self.assertIn("rtc-quality", separate["fallback"]["intents"])

    def test_suppression_respects_subject_and_symptom_occurrence(self):
        for separator in ("和", "同时", "，", "。"):
            text = f"老师声音忽大忽小{separator}学生声音很小"
            with self.subTest(text=text):
                items = classifier.classify(text)["categories"]
                self.assertEqual({(c["id"], c["subject_role"]) for c in items}, {
                    ("audio-volume-fluctuation", "teacher"), ("audio-low-volume", "student")})
        self.assertEqual([c["id"] for c in classifier.classify("老师声音忽大忽小")["categories"]], ["audio-volume-fluctuation"])
        items = classifier.classify("老师声音忽大忽小同时麦克风声音很小")["categories"]
        self.assertEqual({c["id"] for c in items}, {"audio-volume-fluctuation", "audio-low-volume"})

    def test_urls_preserve_surrounding_text_and_hide_signed_values(self):
        suffix = "教室ID：123456789，用户ID：23456789，播放音频进度条不动"
        for prefix in (
            "链接 https://example.com/a，", "链接 https://example.com/a。",
            "链接 ｈｔｔｐｓ：／／example.com/a?sig=SECRET，",
            "[资料](https://example.com/a_(b)?sig=SECRET&x=1,2);",
            "<https://example.com/a?sig=SECRET&x=1,2>，",
        ):
            with self.subTest(prefix=prefix):
                result = classifier.classify(prefix + suffix)
                self.assertEqual([c["id"] for c in result["categories"]], ["courseware-audio-playback"])
                self.assertEqual(result["identity"]["channel_names"], ["123456789"])
                self.assertEqual(result["identity"]["user_uids"], ["23456789"])
                self.assertEqual(result["attachment_link_count"], 1)
                self.assertNotIn("SECRET", json.dumps(result))
        label = classifier.classify("[播放音频进度条不动](https://example.com/a?sig=SECRET)")
        self.assertEqual([c["id"] for c in label["categories"]], ["courseware-audio-playback"])
        ambiguous = classifier.classify("链接 https://example.com/中文路径后面没有声音")
        self.assertIn("url_boundary_uncertain", ambiguous["review_reasons"])
        self.assertTrue(ambiguous["fallback"]["required"])
        self.assertNotIn("中文路径", json.dumps(ambiguous, ensure_ascii=False))
        crossing = classifier.classify("播放音频](http://a?k=S)时长为0")
        self.assertNotIn("http://", json.dumps(crossing))
        self.assertNotIn("k=S", json.dumps(crossing))

    def test_playback_context_is_bounded_and_punctuation_independent(self):
        for separator in ("，", "。", "\n"):
            result = classifier.classify(f"课件音频正在播放{separator}没有声音。")
            self.assertEqual([c["id"] for c in result["categories"]], ["courseware-audio-playback"])
        for text in (
            "课件音频正常。学生麦克风没有声音。",
            "老师播放课件音频。学生没有声音。",
            "课件音频正在播放。另一个问题，没有声音。",
            "课件音频正在播放。设备是Windows。没有声音。",
            "课件音频正在播放。\n\n没有声音。",
            "课件音频正在播放。共享电脑声音没有声音。",
        ):
            with self.subTest(text=text):
                self.assertEqual([c["id"] for c in classifier.classify(text)["categories"]], ["audio-no-sound"])
        replay = classifier.classify("回放异常。没有声音。")
        self.assertEqual(replay["categories"], [])
        self.assertIn("replay-investigation", replay["fallback"]["intents"])
        bound = classifier.classify("老师ID:80000001。老师播放课件音频。没有声音。")
        self.assertEqual(bound["categories"][0]["subject_role"], "teacher")
        self.assertEqual(bound["categories"][0]["subject_uid"], "80000001")

    def test_explicit_uids_keep_same_role_subjects_separate(self):
        result = classifier.classify("老师ID:80000001声音忽大忽小和老师ID:80000002声音很小")
        self.assertEqual({(c["id"], c["subject_uid"]) for c in result["categories"]}, {
            ("audio-volume-fluctuation", "80000001"), ("audio-low-volume", "80000002")})

    def test_context_is_invalidated_when_channel_or_target_changes(self):
        context = {"channel_name": "111111111", "teacher_uid": "22222222", "student_uid": "33333333",
                   "user_uid": "22222222", "platform": "Windows"}
        changed = classifier.classify("教室ID：999999999，用户ID：88888888，老师有回声", context)
        self.assertEqual(changed["identity"]["channel_names"], ["999999999"])
        self.assertEqual(changed["identity"]["teacher_uids"], [])
        self.assertEqual(changed["identity"]["student_uids"], [])
        self.assertEqual(changed["identity"]["platforms"], [])
        self.assertIsNone(changed["categories"][0]["subject_uid"])
        changed_user = classifier.classify("用户ID：88888888，老师有回声", context)
        self.assertEqual(changed_user["identity"]["channel_names"], ["111111111"])
        self.assertEqual(changed_user["identity"]["teacher_uids"], [])
        self.assertEqual(changed_user["identity"]["platforms"], [])
        switched_role = classifier.classify("学生还有杂音", context)
        self.assertEqual(switched_role["identity"]["platforms"], [])
        self.assertEqual(switched_role["identity"]["user_uids"], [])
        self.assertEqual(switched_role["categories"][0]["subject_uid"], "33333333")
        unknown_role = classifier.classify("学生还有杂音", {"teacher_uid": "22222222", "platform": "Windows"})
        self.assertIsNone(unknown_role["categories"][0]["subject_uid"])
        self.assertEqual(unknown_role["identity"]["platforms"], [])
        same = classifier.classify("教室ID：111111111，用户ID：22222222，老师仍有回声", context)
        self.assertEqual(same["categories"][0]["subject_uid"], "22222222")
        self.assertEqual(same["categories"][0]["platforms"], ["Windows"])
        followup = classifier.classify("还是有回声", context)
        self.assertEqual(followup["categories"][0]["subject_uid"], "22222222")
        self.assertEqual(followup["categories"][0]["platforms"], ["Windows"])
        another = classifier.classify("另一个学生声音很小", context)
        self.assertIsNone(another["categories"][0]["subject_uid"])
        self.assertEqual(another["identity"]["platforms"], [])
        unscoped = classifier.classify("没有声音", {"platform": "Windows"})
        self.assertEqual(unscoped["identity"]["platforms"], [])
        self.assertIn("context_platform_unbound", unscoped["identity"]["context_warnings"])

    def test_match_evidence_uses_actual_original_offsets(self):
        text = "[声音很小](https://example.com/声音很小?token=SECRET)，老师声音很小。学生声音很小"
        result = classifier.classify(text)
        spans = []
        for item in result["categories"]:
            for evidence in item["evidence"]:
                span = evidence["source_span"]
                self.assertEqual(text[span["start"]:span["end"]], evidence["text"])
                spans.append(span["start"])
        self.assertIn(text.index("声音很小", text.index("老师")), spans)
        self.assertIn(text.index("声音很小", text.index("学生")), spans)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_unseen_positive_phrases(self):
        cases = {"画面侧着显示": "video-rotation", "视频有马赛克": "video-blur", "摄像头打不开": "camera-capture-failure", "声音一会大一会小": "audio-volume-fluctuation", "ios airplay希望从手机扬声器出声": "airplay-output-routing", "反复重连": "connection-failure", "视频落后于声音": "av-out-of-sync"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertIn(expected, [c["id"] for c in classifier.classify(text)["categories"]])

    def test_cli_stdin_file_output_and_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "question.txt", Path(tmp) / "result.json"
            source.write_text("播放音频进度条不动", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/classify_issue.py"), "--input", str(source), "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), json.loads(output.read_text(encoding="utf-8")))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/classify_issue.py")], input="", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["ok"])

    def test_invalid_input_and_context(self):
        for text in ("", "  ", "声" * (classifier.MAX_INPUT_BYTES // 3 + 1)):
            with self.assertRaises(ValueError):
                classifier.classify(text)
        with self.assertRaises(ValueError):
            classifier.classify("没声音", {"student_uid": 70000001})
        with self.assertRaises(ValueError):
            classifier.classify("没声音", [])

    def test_matched_text_preserves_customer_spelling(self):
        text = "老师无法播放ＭＰ３文件"
        result = classifier.classify(text)
        matched = result["categories"][0]["matched_text"]
        self.assertIn(matched, text)
        self.assertIn("ＭＰ３", matched)

    def test_bad_rules_and_stdin_are_reported_without_raw_text(self):
        with self.assertRaises(ValueError):
            classifier.classify("没声音", rules=[])
        result = subprocess.run([sys.executable, str(ROOT / "scripts/classify_issue.py")], input="学生画面侧着", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["categories"][0]["id"], "video-rotation")
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "rules.json"
            rules.write_text("[]", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/classify_issue.py"), "--rules", str(rules)], input="https://example.invalid/?token=SECRET", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("SECRET", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
