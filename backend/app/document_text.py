"""投稿文档的本地正文提取：DOCX、Markdown、TXT、CSV。"""
import io
import zipfile
from xml.etree import ElementTree as ET

MAX_PREVIEW_CHARS = 120_000
TEXT_EXTENSIONS = {".txt", ".md", ".csv"}
DOCX_EXTENSIONS = {".docx"}
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W_VAL = f"{{{W_NS}}}val"
W_TEXT = f"{{{W_NS}}}t"
W_TAB = f"{{{W_NS}}}tab"
W_BREAK = f"{{{W_NS}}}br"


def valid_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                return False
            if archive.getinfo("word/document.xml").file_size > 50 * 1024 * 1024:
                return False
            ET.fromstring(archive.read("word/document.xml"))
        return True
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError):
        return False


def _paragraph_text(paragraph: ET.Element) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == W_TEXT:
            parts.append(node.text or "")
        elif node.tag == W_TAB:
            parts.append("\t")
        elif node.tag == W_BREAK:
            parts.append("\n")
    return "".join(parts).strip()


def _paragraph_markdown(paragraph: ET.Element) -> str:
    text = _paragraph_text(paragraph)
    if not text:
        return ""
    style_node = paragraph.find("./w:pPr/w:pStyle", NS)
    style = (style_node.get(W_VAL, "") if style_node is not None else "").lower()
    if style.startswith("heading") and style[7:].isdigit():
        level = min(max(int(style[7:]), 1), 4)
        return f"{'#' * level} {text}"
    if style.startswith("标题") and style[2:].isdigit():
        level = min(max(int(style[2:]), 1), 4)
        return f"{'#' * level} {text}"
    if paragraph.find("./w:pPr/w:numPr", NS) is not None:
        return f"- {text}"
    return text


def _table_markdown(table: ET.Element) -> str:
    rows = []
    for row in table.findall("./w:tr", NS):
        cells = []
        for cell in row.findall("./w:tc", NS):
            paragraphs = [_paragraph_text(p) for p in cell.findall(".//w:p", NS)]
            cells.append(" / ".join(x for x in paragraphs if x).replace("|", "｜"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def parse_docx(content: bytes) -> dict:
    if not valid_docx(content):
        raise ValueError("DOCX 文件损坏或不是有效的 Word 文档")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX 中未找到正文")
    blocks = []
    for child in body:
        if child.tag == f"{{{W_NS}}}p":
            text = _paragraph_markdown(child)
            if text:
                blocks.append(text)
        elif child.tag == f"{{{W_NS}}}tbl":
            text = _table_markdown(child)
            if text:
                blocks.append(text)
    text = "\n\n".join(blocks)
    return {"text": text[:MAX_PREVIEW_CHARS], "truncated": len(text) > MAX_PREVIEW_CHARS,
            "format": "markdown", "parser": "docx_xml_v1",
            "paragraphCount": len(root.findall(".//w:p", NS)),
            "tableCount": len(root.findall(".//w:tbl", NS))}


def extract_document(content: bytes, extension: str) -> dict:
    extension = extension.lower()
    if not extension.startswith("."):
        extension = "." + extension
    if extension in DOCX_EXTENSIONS:
        return parse_docx(content)
    if extension not in TEXT_EXTENSIONS:
        raise ValueError("该文件类型不支持文本预览")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文本文件必须使用 UTF-8 编码") from exc
    return {"text": text[:MAX_PREVIEW_CHARS], "truncated": len(text) > MAX_PREVIEW_CHARS,
            "format": "markdown" if extension == ".md" else "text",
            "parser": "utf8_text_v1", "paragraphCount": len(text.splitlines()),
            "tableCount": 0}
