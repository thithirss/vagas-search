import unittest

from vagas.sources.linkedin import LinkedInSource


class ScrollTopJsTest(unittest.TestCase):
    def test_js_resets_inner_scroll_containers(self):
        js = LinkedInSource.SCROLL_TOP_JS
        self.assertIn("window.scrollTo(0, 0)", js)
        self.assertIn("scrollTop = 0", js)
        self.assertTrue(callable(LinkedInSource._scroll_top))


if __name__ == "__main__":
    unittest.main()
