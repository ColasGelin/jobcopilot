from pathlib import Path

from jobcopilot.cv.bank import load_bank
from jobcopilot.cv.content import ExperienceBlock, ProjectItem, main_content
from jobcopilot.cv.render import SECTION_TITLES, render_html, render_pdf

EXAMPLE = Path(__file__).parent / "fixtures" / "cv.example.yaml"


def example():
    return main_content(load_bank(EXAMPLE))


def test_render_html_contains_main_items():
    content = example()
    html = render_html(content)
    assert "Alex Martin" in html and "AI Engineer" in html
    for block in content.experience:
        for bullet in block.bullets:
            assert bullet in html
    assert "Lifeguard at a city pool" in html and "Chess, climbing" in html


def test_render_escapes_text():
    content = example()
    content.experience[0].bullets[0] = "<b>x</b> & y"
    html = render_html(content)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in html
    assert "<b>x</b>" not in html


def test_render_pdf_one_page():
    result = render_pdf(example())
    assert result.pdf[:4] == b"%PDF"
    assert result.pages == 1


def test_render_pdf_counts_overflow():
    content = example()
    content.experience[0].bullets = [f"Long bullet number {i} describing work in detail." for i in range(60)]
    assert render_pdf(content).pages >= 2


def test_in_progress_label_translated():
    content = example().model_copy(update={"language": "es"})
    content.engineering.append(ProjectItem(name="Agent Lab", text="Agents.", in_progress=True))
    html = render_html(content)
    assert "(en curso)" in html
    assert SECTION_TITLES["es"]["experience"] in html


def test_web_preview_uses_asset_urls():
    html = render_html(example(), for_web=True)
    assert "/cv-assets/inter-400.woff" in html and "file://" not in html


def test_location_optional():
    content = example()
    content.experience = [ExperienceBlock(title="Dev", company="X", dates="2024", bullets=["Did things."])]
    assert "Dev · X" in render_html(content)


def test_section_title_override():
    content = example().model_copy(update={"section_titles": {"en": {"engineering": "Engineering · Code School"}}})
    html = render_html(content)
    assert "Engineering · Code School" in html and ">Projects<" not in html
