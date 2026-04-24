import os
import win32com.client

WD_FORMAT_XML_DOCUMENT = 16  # docx

def convert_doc_to_docx_if_needed(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext != ".doc":
        return file_path

    abs_path = os.path.abspath(file_path)
    docx_path = os.path.splitext(abs_path)[0] + ".docx"

    word = None
    doc = None
    try:
        # DispatchEx tạo instance riêng, đỡ xung đột khi nhiều job
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        doc = word.Documents.Open(abs_path, ReadOnly=True)
        doc.SaveAs(docx_path, FileFormat=WD_FORMAT_XML_DOCUMENT)
        doc.Close(False)
        doc = None

        # Chỉ giữ docx
        try:
            os.remove(abs_path)
        except Exception:       
            pass

        return docx_path

    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass