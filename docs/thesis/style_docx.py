"""Polish the look of the built dissertation, on top of the Hull template.

Called by assemble.py after pandoc. It keeps Hull's template (page size,
margins, gutter, footers, Calibri) and only:
  * defines the styles pandoc uses but the Hull template lacks
    (Body Text, First Paragraph, Compact, Table, Block Text, Verbatim Char),
  * gives headings, captions and tables one calm, consistent look
    (one accent colour: Hull-template navy 1F3864),
  * shows chapter headings as "Chapter 1: Introduction".

Sizes are in half-points (sz 22 = 11 pt); spacing in twentieths of a point.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

NAVY = "1F3864"     # the Hull template's own accent (accent5, shade 80)
INK2 = "404040"
RULE = "BFBFBF"

STYLES = {
    # ---- body ---------------------------------------------------------
    "Normal": """<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:before="0" w:after="160" w:line="360" w:lineRule="auto"/></w:pPr>
<w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>""",
    "BodyText": """<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body Text"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:jc w:val="both"/></w:pPr></w:style>""",
    "FirstParagraph": """<w:style w:type="paragraph" w:styleId="FirstParagraph"><w:name w:val="First Paragraph"/><w:basedOn w:val="BodyText"/><w:next w:val="BodyText"/><w:qFormat/></w:style>""",
    # Compact = list items and table cells.
    "Compact": """<w:style w:type="paragraph" w:styleId="Compact"><w:name w:val="Compact"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:before="20" w:after="40" w:line="264" w:lineRule="auto"/></w:pPr></w:style>""",
    "BlockText": f"""<w:style w:type="paragraph" w:styleId="BlockText"><w:name w:val="Block Text"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:pBdr><w:left w:val="single" w:sz="12" w:space="10" w:color="{RULE}"/></w:pBdr><w:spacing w:before="120" w:after="200" w:line="300" w:lineRule="auto"/><w:ind w:left="454" w:right="454"/></w:pPr>
<w:rPr><w:color w:val="{INK2}"/></w:rPr></w:style>""",
    "VerbatimChar": """<w:style w:type="character" w:customStyle="1" w:styleId="VerbatimChar"><w:name w:val="Verbatim Char"/><w:basedOn w:val="DefaultParagraphFont"/>
<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:color w:val="3A3A3A"/><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>""",
    "Hyperlink": f"""<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:basedOn w:val="DefaultParagraphFont"/><w:uiPriority w:val="99"/><w:unhideWhenUsed/>
<w:rPr><w:color w:val="{NAVY}"/><w:u w:val="none"/></w:rPr></w:style>""",

    # ---- headings (numbering kept from the Hull template: numId 1) ------
    "Heading1": f"""<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:link w:val="Heading1Char"/><w:uiPriority w:val="9"/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:pageBreakBefore/><w:numPr><w:numId w:val="1"/></w:numPr>
<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="10" w:color="{NAVY}"/></w:pBdr>
<w:spacing w:before="600" w:after="480" w:line="240" w:lineRule="auto"/><w:ind w:left="0" w:firstLine="0"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/><w:b/><w:color w:val="{NAVY}"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>""",
    "Heading2": f"""<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:link w:val="Heading2Char"/><w:uiPriority w:val="9"/><w:unhideWhenUsed/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr>
<w:spacing w:before="400" w:after="140" w:line="240" w:lineRule="auto"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/><w:b/><w:color w:val="{NAVY}"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>""",
    "Heading3": """<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:link w:val="Heading3Char"/><w:uiPriority w:val="9"/><w:unhideWhenUsed/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:numPr><w:ilvl w:val="2"/><w:numId w:val="1"/></w:numPr>
<w:spacing w:before="280" w:after="100" w:line="240" w:lineRule="auto"/><w:outlineLvl w:val="2"/></w:pPr>
<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:hAnsiTheme="majorHAnsi" w:cstheme="majorBidi"/><w:b/><w:color w:val="262626"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>""",
    # Unnumbered front-matter headings look like chapter headings.
    "Headingfrontmatter": f"""<w:style w:type="paragraph" w:customStyle="1" w:styleId="Headingfrontmatter"><w:name w:val="Heading front matter"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:pageBreakBefore/>
<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="10" w:color="{NAVY}"/></w:pBdr>
<w:spacing w:before="600" w:after="480" w:line="240" w:lineRule="auto"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:hAnsiTheme="majorHAnsi"/><w:b/><w:color w:val="{NAVY}"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>""",
    "Headingbackmatter": """<w:style w:type="paragraph" w:customStyle="1" w:styleId="Headingbackmatter"><w:name w:val="Heading back matter"/><w:basedOn w:val="Heading1"/><w:next w:val="BodyText"/><w:qFormat/>
<w:pPr><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr></w:style>""",

    # ---- captions, contents --------------------------------------------
    "Caption": f"""<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:uiPriority w:val="35"/><w:unhideWhenUsed/><w:qFormat/>
<w:pPr><w:keepLines/><w:spacing w:before="60" w:after="360" w:line="252" w:lineRule="auto"/></w:pPr>
<w:rPr><w:color w:val="{INK2}"/><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>""",
    "TOC1": """<w:style w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8494"/></w:tabs><w:spacing w:before="160" w:after="40" w:line="240" w:lineRule="auto"/></w:pPr>
<w:rPr><w:b/></w:rPr></w:style>""",
    "TOC2": """<w:style w:type="paragraph" w:styleId="TOC2"><w:name w:val="toc 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8494"/></w:tabs><w:spacing w:before="0" w:after="40" w:line="240" w:lineRule="auto"/><w:ind w:left="284"/></w:pPr></w:style>""",
    "TOC3": """<w:style w:type="paragraph" w:styleId="TOC3"><w:name w:val="toc 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="39"/><w:unhideWhenUsed/>
<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8494"/></w:tabs><w:spacing w:before="0" w:after="20" w:line="240" w:lineRule="auto"/><w:ind w:left="567"/></w:pPr>
<w:rPr><w:color w:val="404040"/><w:sz w:val="20"/></w:rPr></w:style>""",

    # ---- tables: rules only, no grid; navy header rule -------------------
    "Table": f"""<w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/><w:basedOn w:val="TableNormal"/><w:uiPriority w:val="59"/><w:qFormat/>
<w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>
<w:tblPr><w:jc w:val="center"/>
<w:tblBorders><w:top w:val="single" w:sz="8" w:space="0" w:color="{NAVY}"/><w:bottom w:val="single" w:sz="8" w:space="0" w:color="{NAVY}"/><w:insideH w:val="single" w:sz="2" w:space="0" w:color="D9D9D9"/></w:tblBorders>
<w:tblCellMar><w:top w:w="45" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="45" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>
<w:tblStylePr w:type="firstRow"><w:pPr><w:keepNext/></w:pPr><w:rPr><w:b/><w:color w:val="{NAVY}"/></w:rPr>
<w:tcPr><w:tcBorders><w:bottom w:val="single" w:sz="8" w:space="0" w:color="{NAVY}"/></w:tcBorders><w:shd w:val="clear" w:color="auto" w:fill="EEF2F8"/></w:tcPr></w:tblStylePr></w:style>""",
}

# Title-page and table-cell paragraphs keep these sizes regardless of Normal.
STYLE_ID = re.compile(r'<w:style\b[^>]*w:styleId="([^"]+)"[^>]*>.*?</w:style>', re.S)


def _restyle(styles_xml: str) -> str:
    present = {m.group(1): m for m in STYLE_ID.finditer(styles_xml)}
    for sid, xml in STYLES.items():
        xml = re.sub(r">\s+<", "><", xml)
        if sid in present:
            styles_xml = styles_xml.replace(present[sid].group(0), xml, 1)
        else:
            styles_xml = styles_xml.replace("</w:styles>", xml + "</w:styles>", 1)
    return styles_xml


def _chapter_label(numbering_xml: str) -> str:
    """Heading 1 numbering: 'Chapter 1: Introduction' (space, not a tab)."""
    def fix(m: re.Match) -> str:
        block = m.group(0)
        if 'w:val="Heading1"' not in block:
            return block
        def lvl0(l: re.Match) -> str:
            s = l.group(0).replace('w:val="Chapter %1"', 'w:val="Chapter %1:"')
            s = re.sub(r"<w:suff [^>]*/>", "", s)
            return s.replace("<w:lvlText", '<w:suff w:val="space"/><w:lvlText', 1)
        return re.sub(r'<w:lvl w:ilvl="0".*?</w:lvl>', lvl0, block, count=1, flags=re.S)
    return re.sub(r"<w:abstractNum\b.*?</w:abstractNum>", fix, numbering_xml, flags=re.S)


def polish(docx: Path) -> None:
    tmp = docx.with_suffix(".tmp.docx")
    with zipfile.ZipFile(docx) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                data = _restyle(data.decode("utf-8")).encode("utf-8")
            elif item.filename == "word/numbering.xml":
                data = _chapter_label(data.decode("utf-8")).encode("utf-8")
            elif item.filename == "word/document.xml":
                # Image paths are local (/Users/<name>/...): keep only the file
                # name so no username leaks into hidden metadata (Hull: ID only).
                data = re.sub(rb'(descr|name)="/[^"]*/([^"/]+)"', rb'\1="\2"', data)
            zout.writestr(item, data)
    shutil.move(tmp, docx)
