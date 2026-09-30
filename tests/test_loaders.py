import docx
import openpyxl

from people_alarm.loaders import find_documents, to_content_blocks


def test_docx_and_xlsx_to_text(tmp_path):
    d = docx.Document()
    d.add_paragraph("매월 10일까지 원천세를 신고한다.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "연말정산"
    table.rows[0].cells[1].text = "1월"
    d.save(tmp_path / "manual.docx")

    wb = openpyxl.Workbook()
    wb.active.title = "연간일정"
    wb.active.append(["월", "업무"])
    wb.active.append([3, "법인세 신고"])
    wb.save(tmp_path / "calendar.xlsx")

    (tmp_path / "~$temp.docx").write_bytes(b"lock")
    (tmp_path / "image.png").write_bytes(b"x")

    docs = find_documents(tmp_path)
    assert [p.name for p in docs] == ["calendar.xlsx", "manual.docx"]

    xlsx_text = to_content_blocks(docs[0])[0]["text"]
    assert "[시트: 연간일정]" in xlsx_text and "3 | 법인세 신고" in xlsx_text

    docx_text = to_content_blocks(docs[1])[0]["text"]
    assert "원천세" in docx_text and "연말정산 | 1월" in docx_text


def test_pdf_is_sent_as_document_block(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    block = to_content_blocks(tmp_path / "a.pdf")[0]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
