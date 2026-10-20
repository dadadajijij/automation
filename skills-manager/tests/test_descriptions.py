import unittest

from fastapi import HTTPException

from skill_manager.app import description_from


class DescriptionTests(unittest.TestCase):
    def test_explicit_description_takes_priority(self):
        self.assertEqual(description_from('---\nname: demo\ndescription: "总结 Agora APIs"\n---\n# Title\n\nOther paragraph.'), "总结 Agora APIs")

    def test_yaml_multiline_descriptions(self):
        self.assertEqual(description_from('---\ndescription: >\n  Use this skill\n  for Agora APIs.\n---'), "Use this skill for Agora APIs.")
        self.assertEqual(description_from('---\ndescription: |\n  First line.\n  Second line.\n---'), "First line.\nSecond line.")

    def test_user_example_uses_first_paragraph(self):
        paragraph = "Use this skill when a customer asks about Agora/Shengwang APIs, SDK behavior, error codes, integration, known issues, private parameters, or troubleshooting knowledge that should be retrieved from the managed Bailian knowledge bases."
        self.assertEqual(description_from("# Agora Bailian RAG Skill\n\n" + paragraph + "\n\nNext paragraph."), paragraph)

    def test_missing_or_empty_description_falls_back(self):
        for frontmatter in ("name: demo", "description:", 'description: "  "'):
            self.assertEqual(description_from("---\n" + frontmatter + "\n---\n# Title\n\nFirst wrapped\nparagraph.\n\nSecond."), "First wrapped paragraph.")

    def test_skip_non_paragraph_blocks(self):
        source = "# Title\n\n<!-- comment\ncontinued -->\n\n```text\nCode\n```\n\n- item\n\n    indented code\n\nActual description.\n\nNext."
        self.assertEqual(description_from(source), "Actual description.")
        self.assertEqual(description_from("Title\n=====\n\nActual paragraph."), "Actual paragraph.")
        self.assertEqual(description_from("# Only a title\n\n---\n"), "")

    def test_invalid_frontmatter_or_type(self):
        for source in ('---\ndescription: [bad]\n---', '---\ndescription: 123\n---', '---\ndescription: [\n---', '---\nname: demo', '---\n- bad\n---'):
            with self.subTest(source=source), self.assertRaises(HTTPException):
                description_from(source)

    def test_long_description_is_not_truncated(self):
        description = "A" * 600
        self.assertEqual(description_from("---\ndescription: " + description + "\n---"), description)


if __name__ == "__main__":
    unittest.main()
