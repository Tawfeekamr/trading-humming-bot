#!/usr/bin/env python3
"""Assemble the dissertation into the Hull 771952 Word template.

This script does NOT write or rewrite any prose. It only:
  * cuts the existing chapters out of the source files,
  * puts them into the six-chapter order the Hull brief asks for,
  * removes hand-typed heading numbers (the template numbers headings itself),
  * adds clearly marked "TO WRITE" notes (template "Guidance notes" style)
    wherever a section still has to be written by the author,
  * runs pandoc with the Hull template as the style reference.

Sources:
  docs/dissertation_manuscript.md   abstract, Ch1, Ch4, Ch5, references
  docs/chapters/03_methodology.md   current Chapter 3 + Appendix 3.A

Run:  python3 docs/thesis/assemble.py
Out:  docs/thesis/build/dissertation.md  and  docs/thesis/build/dissertation.docx
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
MANUSCRIPT = (DOCS / "dissertation_manuscript.md").read_text(encoding="utf-8")
CH3 = (DOCS / "chapters" / "03_methodology.md").read_text(encoding="utf-8")
TEMPLATE = HERE / "hull_template.docx"
BUILD = HERE / "build"

STUDENT_ID = "202358755"
TITLE = ("Reward-Induced Abstention and Evaluation Blindness in a "
         "Reinforcement-Learning Trading System: A Corrected-Protocol Case Study")
SUBMISSION_DATE = "January 2027"


# ---------------------------------------------------------------- helpers
def between(text: str, start: str, end: str | None) -> str:
    """Return the text from the line matching `start` up to (not incl.) `end`."""
    s = re.search(start, text, re.M)
    if not s:
        sys.exit(f"start marker not found: {start!r}")
    rest = text[s.start():]
    if end is None:
        return rest
    e = re.search(end, rest[1:], re.M)
    if not e:
        sys.exit(f"end marker not found: {end!r}")
    return rest[: e.start() + 1]


def drop_first_line(block: str) -> str:
    return block.split("\n", 1)[1] if "\n" in block else ""


HEADING_RE = re.compile(
    r"^(#{1,6})\s+(?:Chapter\s+\d+\s*[:—–-]\s*)?(?:\d+(?:\.\d+)*[a-z]?\.?\s+)?(.*)$"
)


def clean(block: str) -> str:
    """Strip hand numbering from headings and drop bare '---' rules."""
    out = []
    for line in block.splitlines():
        if line.strip() == "---":
            continue
        m = HEADING_RE.match(line)
        if m:
            line = f"{m.group(1)} {m.group(2).strip()}"
        out.append(line)
    return "\n".join(out).strip() + "\n"


def demote_headings_to_bold(block: str) -> str:
    """Inside appendices: headings would be numbered as chapter sections,
    so turn them into bold paragraphs instead."""
    return re.sub(r"^#{1,6}\s+(.*)$", r"**\1**", block, flags=re.M)


# Set to True to show the blue "TO WRITE" helper notes in the Word file.
# Off by default: the notes live in WRITING_PLAN.md instead.
SHOW_NOTES = False


def note(text: str) -> str:
    """A 'to do' note in the template's Guidance-notes style (only if SHOW_NOTES)."""
    if not SHOW_NOTES:
        return ""
    return f'\n::: {{custom-style="Guidance notes"}}\n{text.strip()}\n:::\n'


def todo(heading: str, level: int, plan_ref: str, words: str) -> str:
    return (f"\n{'#' * level} {heading}\n"
            + note(f"TO WRITE (author). About {words} words. "
                   f"See WRITING_PLAN.md, {plan_ref}."))


def styled(style: str, text: str) -> str:
    return f'\n::: {{custom-style="{style}"}}\n{text.strip()}\n:::\n'


def front_heading(text: str) -> str:
    # Unnumbered front-matter heading, starts a new page (template style).
    return styled("Heading front matter", text)


def back_heading(text: str) -> str:
    return styled("Heading back matter", text)


def field(instr: str, placeholder: str) -> str:
    """A Word field (TOC / list of figures / list of tables). Word fills it
    in when you right-click it and choose 'Update Field' (or press F9)."""
    return f"""
```{{=openxml}}
<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r><w:r><w:instrText xml:space="preserve"> {instr} </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>{placeholder}</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
```
"""


# ---------------------------------------------------------------- cross-references
# Sections moved into the Hull six-chapter order, so their numbers changed.
# Each map was checked by hand against the context of every reference
# (26 Nov 2026). The source files keep their own numbering; this runs at build.
#
# Text from the old manuscript (Ch1, Ch4, Ch5): its "Section 3.x" refers to the
# OLD compressed Chapter 3 — reward/fee double-count is now 3.2, failure modes 3.7.
RENUM_MANUSCRIPT = {
    "3.1": "3.2", "3.6": "3.7",
    "4.6": "5.1", "4.7": "5.2", "4.8": "4.6",
    "5.1": "6.1", "5.2": "5.3", "5.3": "5.4", "5.3.1": "5.4.1", "5.4": "6.4",
}
# Text from the new Chapter 3 file: its own 3.x numbers are already right.
RENUM_CH3 = {
    "4.6": "5.1", "4.7": "5.2", "4.8": "4.6",
    "5.1": "6.1", "5.2": "5.3", "5.3": "5.4", "5.3.1": "5.4.1", "5.4": "6.4",
}
SECTION_REF = re.compile(r"(Sections?\s+|§)(\d+(?:\.\d+)*)(?![\d.]*\d)")


def renumber(block: str, table: dict[str, str]) -> str:
    block = SECTION_REF.sub(lambda m: m.group(1) + table.get(m.group(2), m.group(2)), block)
    block = re.sub(r"\bChapter (4\.\d)", r"Section \1", block)        # "Chapter 4.1" -> "Section 4.1"
    block = block.replace("manuscript §4.1", "Section 4.1")
    block = block.replace("Appendix 3.A", "Appendix A")
    return block


FIGURES = HERE / "figures"


def figure(png: str, caption: str) -> str:
    """Image + a real Word caption (Figure <chapter>.<n>) so it appears in the
    List of Figures. Numbers update when the document fields are updated."""
    path = FIGURES / png
    if not path.exists():
        return note(f"Missing figure {png}: run make_figures.py")
    cap = (
        '<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">Figure </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> STYLEREF 1 \\s </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:r><w:t>.</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> SEQ Figure \\* ARABIC \\s 1 </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        f'<w:r><w:t xml:space="preserve">: {caption}</w:t></w:r></w:p>'
    )
    # Text width = A4 minus Hull margins and binding gutter = 8,504 twips = 5.9 in.
    return f"\n![]({path}){{width=5.9in}}\n\n```{{=openxml}}\n{cap}\n```\n"


def place_after_section(block: str, heading: str, insert: str) -> str:
    """Insert text at the end of the section whose heading contains `heading`
    (just before the next heading of the same or higher level)."""
    m = re.search(rf"^(#+) [^\n]*{re.escape(heading)}[^\n]*$", block, re.M)
    if not m:
        sys.exit(f"heading not found for figure placement: {heading!r}")
    level = len(m.group(1))
    nxt = re.compile(rf"^#{{1,{level}}} ", re.M).search(block, m.end())
    pos = nxt.start() if nxt else len(block)
    return block[:pos] + insert + "\n" + block[pos:]


def references_as_paragraphs(block: str) -> str:
    # Harvard reference lists are plain alphabetical paragraphs, not bullets.
    return re.sub(r"^- ", "", block, flags=re.M)


# ---------------------------------------------------------------- slices
abstract = drop_first_line(clean(between(MANUSCRIPT, r"^## Abstract", r"^## Table of Contents")))

ch1_existing = drop_first_line(clean(between(MANUSCRIPT, r"^# Chapter 1", r"^# Chapter 3")))

ch3_full = between(CH3, r"^# Chapter 3", r"^## Appendix 3\.A")
# The leading italic paragraph is a drafting note, not dissertation text.
ch3_body = drop_first_line(clean(ch3_full))
ch3_body = re.sub(r"\A\s*\*Draft for revision\..*?\*\n",
                  note("Drafting note from the source file: delete before submission."),
                  ch3_body, count=1, flags=re.S)
ch3_body = re.sub(r"\n\*Note on references:.*?\*\s*\Z", "\n", ch3_body, flags=re.S)
appendix_defects = drop_first_line(clean(between(CH3, r"^## Appendix 3\.A", None)))

ch4_intro_to_45 = drop_first_line(clean(between(MANUSCRIPT, r"^# Chapter 4", r"^## 4\.6 ")))
sec_46 = clean(between(MANUSCRIPT, r"^## 4\.6 ", r"^## 4\.7 "))
sec_47 = clean(between(MANUSCRIPT, r"^## 4\.7 ", r"^## 4\.8 "))
sec_48 = clean(between(MANUSCRIPT, r"^## 4\.8 ", r"^# Chapter 5"))
sec_51 = clean(between(MANUSCRIPT, r"^## 5\.1 ", r"^## 5\.2 "))
sec_52 = clean(between(MANUSCRIPT, r"^## 5\.2 ", r"^## 5\.3 "))
sec_53 = clean(between(MANUSCRIPT, r"^## 5\.3 ", r"^## 5\.4 "))
sec_54 = clean(between(MANUSCRIPT, r"^## 5\.4 ", r"^# References"))
ch1_existing = renumber(ch1_existing, RENUM_MANUSCRIPT)
ch3_body = renumber(ch3_body, RENUM_CH3)
appendix_defects = renumber(appendix_defects, RENUM_CH3)
ch4_intro_to_45 = renumber(ch4_intro_to_45, RENUM_MANUSCRIPT)
sec_46, sec_47, sec_48, sec_51, sec_52, sec_53, sec_54 = (
    renumber(s, RENUM_MANUSCRIPT) for s in (sec_46, sec_47, sec_48, sec_51, sec_52, sec_53, sec_54))

# ---------------------------------------------------------------- figures
# Captions are short factual labels; reword them in your own style if you like.
FIG_NOTE = note("Refer to this figure in your own text before it appears.")
ch3_body = place_after_section(ch3_body, "The system under study", figure(
    "fig_3_1_architecture.png",
    "System architecture: the live machine-learning and execution layers, and the research stack evaluated in this dissertation") + FIG_NOTE)
ch3_body = place_after_section(ch3_body, "The evaluation protocol", figure(
    "fig_3_2_walk_forward.png",
    "Walk-forward design: six chronological folds, each with 180 days of training, a 70-bar embargo and 30 days of testing") + FIG_NOTE)
ch4_intro_to_45 = place_after_section(ch4_intro_to_45, "The learned policy is abstention",
    figure("fig_4_1_time_vs_capital.png",
           "Reported time in market against capital actually deployed, per policy")
    + figure("fig_4_2_exposure_split.png",
             "Exposure gap between the untrained network and the trained agent, split into explicit and concealed abstention")
    + figure("fig_4_3_action_mix.png",
             "Share of bars on which each kind of action was selected, trained agent against untrained network")
    + figure("fig_4_5_flat_vs_trained.png",
             "Trained agent against the do-nothing policy under the training reward, per seed and fold")
    + FIG_NOTE)
ch4_intro_to_45 = place_after_section(ch4_intro_to_45, "Master Performance Benchmark Table", figure(
    "fig_4_4_fold_drawdowns.png",
    "Maximum drawdown per test fold, with medians, for PPO, the Random-Forest baseline and buy-and-hold") + FIG_NOTE)
ch4_intro_to_45 = place_after_section(ch4_intro_to_45, "Statistical power", figure(
    "fig_4_6_detectable_effect.png",
    "Observed return difference against the minimum effect this design could detect") + FIG_NOTE)

references = references_as_paragraphs(drop_first_line(clean(between(MANUSCRIPT, r"^# References", None))))


# ---------------------------------------------------------------- clean chapters
# docs/thesis/chapters/ holds the rewritten, final-numbering chapters. When all
# are present they replace the slices above. Markers inside them:
#   <!-- FIGURE fig_x_y_name -->        -> image + Word caption
#   <!-- SECTION-TO-WRITE: Heading -->  -> empty "## Heading" for the author
CLEAN_DIR = HERE / "chapters"
CLEAN_FILES = {
    "ch1": "ch1_introduction.md", "ch3": "ch3_methodology.md", "ch4": "ch4_results.md",
    "ch5": "ch5_discussion.md", "ch6": "ch6_conclusions.md", "appA": "appendix_a_defect_register.md",
}
USE_CLEAN = all((CLEAN_DIR / f).exists() for f in CLEAN_FILES.values())

CAPTIONS = {
    "fig_3_1_architecture": "System architecture: the live machine-learning and execution layers, and the research stack evaluated in this dissertation",
    "fig_3_2_walk_forward": "Walk-forward design: six chronological folds, each with 180 days of training, a 70-bar embargo and 30 days of testing",
    "fig_4_1_time_vs_capital": "Reported time in market against capital actually deployed, per policy",
    "fig_4_2_exposure_split": "Exposure gap between the untrained network and the trained agent, split into explicit and concealed abstention",
    "fig_4_3_action_mix": "Share of bars on which each kind of action was selected, trained agent against untrained network",
    "fig_4_4_fold_drawdowns": "Maximum drawdown per test fold, with medians, for PPO, the Random-Forest baseline and buy-and-hold",
    "fig_4_5_flat_vs_trained": "Trained agent against the do-nothing policy under the training reward, per seed and fold",
    "fig_4_6_detectable_effect": "Observed return difference against the minimum effect this design could detect",
}


def table_caption(text: str) -> str:
    """'Table 4.1: ...' line -> real Word caption (appears in the List of Tables)."""
    from xml.sax.saxutils import escape
    cap = (
        '<w:p><w:pPr><w:pStyle w:val="Caption"/><w:keepNext/></w:pPr>'
        '<w:r><w:t xml:space="preserve">Table </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> STYLEREF 1 \\s </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:r><w:t>.</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> SEQ Table \\* ARABIC \\s 1 </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        f'<w:r><w:t xml:space="preserve">: {escape(text)}</w:t></w:r></w:p>'
    )
    return f"\n```{{=openxml}}\n{cap}\n```\n"


TABLE_LINE = re.compile(r"^[*_]{0,2}Table \d+\.\d+\s*[:—–-]\s*(.+?)[*_]{0,2}\s*$", re.M)


def load_clean(key: str) -> str:
    text = (CLEAN_DIR / CLEAN_FILES[key]).read_text(encoding="utf-8")
    text = TABLE_LINE.sub(lambda m: table_caption(m.group(1)), text)
    text = re.sub(r"\A\s*# [^\n]*\n", "", text)                      # chapter title added below
    text = re.sub(r"<!--\s*FIGURE\s+(\w+)\s*-->",
                  lambda m: figure(f"{m.group(1)}.png", CAPTIONS[m.group(1)]), text)
    text = re.sub(r"<!--\s*SECTION-TO-WRITE:\s*(.+?)\s*-->", r"\n## \1\n", text)
    return clean(text)


# ---------------------------------------------------------------- document
parts: list[str] = []
add = parts.append

# Title page — student ID only (Hull brief: no name anywhere).
def title_line(text: str, size: int, before: int = 0, after: int = 120,
               bold: bool = False, color: str = "191919") -> str:
    """One centred title-page line (size in half-points)."""
    from xml.sax.saxutils import escape
    b = "<w:b/>" if bold else ""
    return (f'<w:p><w:pPr><w:pStyle w:val="Title"/><w:spacing w:before="{before}" w:after="{after}" '
            f'w:line="276" w:lineRule="auto"/></w:pPr><w:r><w:rPr>{b}<w:color w:val="{color}"/>'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


add("```{=openxml}\n" + "".join([
    title_line(TITLE, 44, before=2400, after=600, bold=True, color="1F3864"),
    title_line("A dissertation submitted in partial fulfilment of the requirements", 24, after=0, color="404040"),
    title_line("for the degree of Master of Science in Artificial Intelligence", 24, after=900, color="404040"),
    title_line("University of Hull", 28, after=120, bold=True),
    title_line("771952 MSc AI Project", 22, after=1400, color="595959"),
    title_line(f"Student ID: {STUDENT_ID}", 26, after=120),
    title_line(SUBMISSION_DATE, 24, color="595959"),
]) + "\n```\n")
add(note("Hull brief: identify yourself by student ID only. Do not put your name "
         "in the document or in the PDF file name."))

add(front_heading("Acknowledgements"))
add(note("TO WRITE (author): 2–3 short sentences. Do not name yourself. "
         "Check with your supervisor whether naming them is fine (it can identify you)."))

add(front_heading("Abstract"))
add(abstract)
add(note("Check the abstract once Chapters 5 and 6 are final. Open item from "
         "Chapter 3 notes: the '58% time in market' wording still needs your pick. "
         "The abstract counts toward the 20,000 words in this plan to be safe."))


add(front_heading("Contents"))
add(field(r'TOC \o "1-3" \h \z \u', "Right-click here and choose Update Field to build the contents."))
add(front_heading("List of Figures"))
add(field(r'TOC \h \z \c "Figure"', "Right-click here and choose Update Field."))
add(front_heading("List of Tables"))
add(field(r'TOC \h \z \c "Table"', "Right-click here and choose Update Field."))

# Chapter 1
add("\n# Introduction\n")
if USE_CLEAN:
    add(load_clean("ch1"))
else:
    add(ch1_existing)
    add(todo("Industrial Relevance", 2, "Chapter 1, item 1.5", "300"))
    add(todo("Contributions", 2, "Chapter 1, item 1.6", "250"))
    add(todo("Structure of the Dissertation", 2, "Chapter 1, item 1.7", "150"))

# Chapter 2
add("\n# Literature Review\n")
if (CLEAN_DIR / "ch2_literature_review.md").exists():
    _ch2 = re.sub(r"\A\s*# [^\n]*\n", "", (CLEAN_DIR / "ch2_literature_review.md").read_text(encoding="utf-8"))
    add(clean(TABLE_LINE.sub(lambda m: table_caption(m.group(1)), _ch2)))
for i, h in enumerate([] if (CLEAN_DIR / "ch2_literature_review.md").exists() else [
    "Reinforcement Learning for Trading",
    "Market Regimes and Supervised Regime Classification",
    "Evaluating Trading Strategies: Backtest Overfitting and Statistical Testing",
    "Reward Design, Reward Misspecification and Abstention",
    "Measuring Exposure: Lessons from Fund Evaluation",
    "Summary and Research Gap",
], start=1):
    add(todo(h, 2, f"Chapter 2, item 2.{i}", "450–600"))

# Chapter 3
add("\n# Methodology\n")
add(load_clean("ch3") if USE_CLEAN else ch3_body)
if (CLEAN_DIR / "ch3_implementation.md").exists():
    add(clean(TABLE_LINE.sub(lambda m: table_caption(m.group(1)),
                             (CLEAN_DIR / "ch3_implementation.md").read_text(encoding="utf-8"))))
else:
    add(todo("System Implementation and Testing", 2, "Chapter 3, item 3.12", "500"))
    add(todo("Project Plan and Risk Management", 2, "Chapter 3, item 3.13", "350"))

# Chapter 4
add("\n# Results\n")
if USE_CLEAN:
    add(load_clean("ch4"))
else:
    add(ch4_intro_to_45)
    add(sec_48)

# Chapter 5
add("\n# Discussion and Recommendations\n")
if USE_CLEAN:
    add(load_clean("ch5"))
else:
    for sec in (sec_46, sec_47, sec_52, sec_53):
        add(sec)
    add(todo("Recommendations for Deployment", 2, "Chapter 5, item 5.5", "500"))

# Chapter 6
add("\n# Conclusions\n")
if USE_CLEAN:
    add(load_clean("ch6"))
else:
    add(sec_51)
    add(todo("Answer to the Research Question", 2, "Chapter 6, item 6.2", "200"))
    add(todo("Reflection on the Project", 2, "Chapter 6, item 6.3", "400"))
    add(sec_54)

# Back matter
add(back_heading("References"))
add(note("Harvard style (Hull LibGuide). Chapter 2 will add more sources — "
         "see WRITING_PLAN.md, part 5. Check every entry yourself."))
_refs_file = CLEAN_DIR / "references.md"
add(re.sub(r"\A\s*# [^\n]*\n", "", _refs_file.read_text(encoding="utf-8")) if _refs_file.exists() else references)

add(back_heading("Appendix A: Evaluation-Defect Register"))
add(demote_headings_to_bold(load_clean("appA") if USE_CLEAN else appendix_defects))
for title, fname in [("Appendix B: Installation and Running Instructions", "appendix_b_install_run.md"),
                     ("Appendix C: System Architecture and Test Summary", "appendix_c_architecture_tests.md")]:
    add(back_heading(title))
    f = CLEAN_DIR / fname
    if f.exists():
        body = re.sub(r"\A\s*# [^\n]*\n", "", f.read_text(encoding="utf-8"))
        add(demote_headings_to_bold(clean(body)))
add(back_heading("Appendix D: Ethics Approval"))
add(note("Insert the faculty ethics form PDF (submitted for the 6 Aug 2026 deadline)."))

md = "\n".join(parts)
BUILD.mkdir(exist_ok=True)
(BUILD / "dissertation.md").write_text(md, encoding="utf-8")

# ---------------------------------------------------------------- word count
def count_words(s: str) -> int:
    s = re.sub(r"```.*?```", " ", s, flags=re.S)        # raw field blocks
    s = re.sub(r":::.*?:::", " ", s, flags=re.S)         # notes / styled blocks
    return len(re.findall(r"[A-Za-z0-9][\w'’.\-–%$]*", s))

chapters = md[md.find("\n# Introduction"):md.find("Heading back matter")]  # excl. refs + appendices
print(f"Words counted toward the 20,000 limit (abstract + six chapters, notes excluded): "
      f"{count_words(abstract) + count_words(chapters)}")

# ---------------------------------------------------------------- pandoc
cmd = ["pandoc", str(BUILD / "dissertation.md"),
       "-f", "markdown+tex_math_dollars+pipe_tables",
       "--reference-doc", str(TEMPLATE),
       "-o", str(BUILD / "dissertation.docx")]
subprocess.run(cmd, check=True)


def ask_word_to_update_fields(docx_path: Path) -> None:
    """Set <w:updateFields/> so Word offers to refresh the contents, lists and
    figure numbers when the file is opened."""
    import shutil
    import zipfile
    tmp = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(docx_path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/settings.xml" and b"updateFields" not in data:
                data = re.sub(rb"(<w:settings[^>]*>)", rb'\1<w:updateFields w:val="true"/>', data, count=1)
            zout.writestr(item, data)
    shutil.move(tmp, docx_path)


sys.path.insert(0, str(HERE))
from style_docx import polish  # noqa: E402

polish(BUILD / "dissertation.docx")
ask_word_to_update_fields(BUILD / "dissertation.docx")
print(f"Wrote {BUILD / 'dissertation.docx'}")
