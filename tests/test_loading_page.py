import unittest

from core.loading import loading_page_html
from core.plot import DARK_THEME, LIGHT_THEME, PlotStyle


class LoadingPageContractTest(unittest.TestCase):
    def test_each_style_uses_its_plot_theme_colors(self):
        # Given: each supported loading-page style and its established plot theme.
        style_themes = {
            PlotStyle.LIGHT: LIGHT_THEME,
            PlotStyle.DARK: DARK_THEME,
        }

        for style, theme in style_themes.items():
            with self.subTest(style=style):
                # When: the loading page is generated for the style.
                document = loading_page_html(style)

                # Then: browser CSS uses the theme background, text, and grid colors.
                self.assertIn(theme.background_color, document)
                self.assertIn(theme.text_color, document)
                self.assertIn(theme.grid_color, document)

    def test_spinner_is_centered_and_uses_css_border(self):
        # Given: a generated light loading page.
        document = loading_page_html(PlotStyle.LIGHT)

        # When: the browser consumes the spinner stylesheet.
        # Then: the spinner is a centered bordered element, not an image asset.
        self.assertRegex(document, r"(?s)\.spinner\s*\{[^}]*border\s*:")
        self.assertRegex(document, r"(?s)\.spinner\s*\{[^}]*position\s*:\s*absolute")
        self.assertRegex(document, r"(?s)\.spinner\s*\{[^}]*top\s*:\s*50%")
        self.assertRegex(document, r"(?s)\.spinner\s*\{[^}]*left\s*:\s*50%")
        self.assertRegex(document, r"(?s)\.spinner\s*\{[^}]*transform\s*:\s*translate\(")

    def test_spinner_supports_standard_and_webkit_rotation(self):
        # Given: a generated loading page for a QtWebKit-compatible browser.
        document = loading_page_html(PlotStyle.DARK)

        # When: the browser reads the spinner animation declarations.
        # Then: both standard and prefixed animation paths rotate the spinner.
        self.assertRegex(document, r"animation\s*:\s*spin\b")
        self.assertRegex(document, r"-webkit-animation\s*:\s*spin\b")
        self.assertRegex(document, r"@keyframes\s+spin\b")
        self.assertRegex(document, r"@-webkit-keyframes\s+spin\b")

    def test_generated_page_is_asset_free_and_theme_specific(self):
        # Given: both theme variants are generated without external page assets.
        light_document = loading_page_html(PlotStyle.LIGHT)
        dark_document = loading_page_html(PlotStyle.DARK)

        # When: the browser receives either generated document.
        # Then: no GIF or Plotly dependency is referenced, and themes differ.
        self.assertNotIn("loading.gif", light_document.lower())
        self.assertNotIn("loading.gif", dark_document.lower())
        self.assertNotIn("plotly", light_document.lower())
        self.assertNotIn("plotly", dark_document.lower())
        self.assertNotEqual(light_document, dark_document)


if __name__ == "__main__":
    unittest.main()
