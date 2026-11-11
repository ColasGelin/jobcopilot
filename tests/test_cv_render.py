from pathlib import Path

import pytest

from jobcopilot.cv.bank import load_bank
from jobcopilot.cv.content import ExperienceBlock, ProjectItem, main_content
from jobcopilot.cv.render import DENSER, SECTION_TITLES, CvTooLong, render_html, render_pdf

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


def long_cv():
    content = example()
    content.experience[0].bullets = [f"Bullet {i}: built and shipped a feature end to end, from design to production." for i in range(14)]
    content.engineering = [ProjectItem(name=f"Project {i}", text="A project described in two full lines of text, " * 6) for i in range(4)]
    content.hackathons = [ProjectItem(name=f"Hack {i}", text="A hackathon entry described at some length, " * 6) for i in range(3)]
    return content


def test_long_cv_is_shortened_to_one_page_least_relevant_first():
    content = long_cv()
    assert render_pdf(content, one_page=False).pages >= 2

    result = render_pdf(content)
    assert result.pages == 1 and result.removed
    assert result.content.density == len(DENSER)  # the layout was tightened as far as it goes first
    assert result.removed[0] == "hackathons: Hack 2"  # then the last = least relevant item goes
    fitted = result.content
    assert fitted.experience[0].bullets[0] == content.experience[0].bullets[0]  # the most relevant stays
    assert fitted.engineering and fitted.engineering[0].name == "Project 0"
    assert len(fitted.experience[0].bullets) >= 1 and fitted.contact == content.contact
    again = render_pdf(fitted)  # what is saved renders to the same single page
    assert again.pages == 1 and again.removed == []


def test_slightly_long_cv_is_fitted_by_layout_alone():
    content = example()
    extra = ProjectItem(name="Extra", text="A project described in two full lines of text, " * 3)
    content.engineering = content.engineering + [extra]
    while render_pdf(content, one_page=False).pages == 1:  # grow it just past one page
        content.engineering = content.engineering + [extra]
    result = render_pdf(content)
    assert result.pages == 1 and result.removed == [] and result.content.density >= 1
    assert result.content.engineering == content.engineering
    assert DENSER[0] in render_html(result.content) and DENSER[0] not in render_html(content)


def test_cv_that_fits_is_untouched():
    content = example()
    result = render_pdf(content)
    assert result.content == content and result.removed == []


def test_cv_too_long_even_when_trimmed():
    content = example().model_copy(update={"summary": "Very long summary text. " * 900})
    with pytest.raises(CvTooLong):
        render_pdf(content)


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
