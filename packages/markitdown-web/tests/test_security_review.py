"""Regression tests for adversarial inputs found in the security review."""

from __future__ import annotations

import io
import zipfile

import pytest

from markitdown_web.conversion import ConversionError
from markitdown_web.worker import validate_office


def docx_with_retargeted_xml_part(encoding: str = "utf-8") -> bytes:
    """An Office XML part need not have an .xml extension.

    This harmless entity was expanded by Mammoth when the preflight scanned
    only filenames ending in .xml or .rels. No external resource is referenced.
    """
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_type_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    entity_part = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<!DOCTYPE w:document [<!ENTITY safe "LOCAL_ENTITY_WAS_EXPANDED">]>'
        f'<w:document xmlns:w="{word_ns}"><w:body>'
        "<w:p><w:r><w:t>&safe;</w:t></w:r></w:p>"
        "</w:body></w:document>"
    ).encode(encoding)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{content_type_ns}">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/alternate.bin" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{relationship_ns}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" '
            'Target="word/alternate.bin"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{word_ns}"><w:body/></w:document>',
        )
        archive.writestr("word/alternate.bin", entity_part)
    return buffer.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_retargeted_docx_part_cannot_bypass_entity_preflight(encoding):
    with pytest.raises(ConversionError):
        validate_office(docx_with_retargeted_xml_part(encoding), ".docx")


def test_retargeted_docx_entity_is_a_per_document_error(client, request_headers):
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files={"files": ("retargeted.docx", docx_with_retargeted_xml_part())},
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["error"]
    assert result["markdown"] == ""
    assert result["html"] == ""
    assert "LOCAL_ENTITY_WAS_EXPANDED" not in response.text
