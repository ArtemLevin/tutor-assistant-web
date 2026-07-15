from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath

import httpx

from tutor_assistant_web.providers.resilience import CircuitBreaker
from tutor_assistant_web.shared.contracts import (
    ArtifactStorage,
    DocumentBuildRequest,
    DocumentBuildResult,
    DocumentOutput,
    StoredArtifact,
)


class DocumentEngineError(RuntimeError):
    pass


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _tex_source(request: DocumentBuildRequest) -> str:
    sections = []
    for material in request.materials:
        body = _latex_escape(material.content).replace("\n", "\n\n")
        sections.append(f"\\section*{{{_latex_escape(material.title)}}}\n{body}")
    if not sections:
        sections.append(r"\section*{Материалы}\nМатериалы не были сформированы.")
    return (
        "\\documentclass[12pt,a4paper]{article}\n"
        "\\usepackage[T2A]{fontenc}\n\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[russian]{babel}\n\\usepackage[margin=2cm]{geometry}\n"
        "\\usepackage{parskip}\n\\begin{document}\n"
        f"\\title{{{_latex_escape(request.title)}}}\n\\date{{}}\n\\maketitle\n"
        + "\n\n".join(sections)
        + "\n\\end{document}\n"
    )


def _html_source(request: DocumentBuildRequest) -> str:
    sections = "".join(
        f"<section><h2>{html.escape(item.title)}</h2>"
        f'<div class="content">'
        f"{html.escape(item.content).replace(chr(10), '<br>')}</div></section>"
        for item in request.materials
    )
    payload = json.dumps(request.evidence, ensure_ascii=False).replace("</", "<\\/")
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(request.title)}</title>"
        "<style>body{max-width:850px;margin:48px auto;padding:0 24px;font:17px/1.6 system-ui;"
        "color:#17202a}h1,h2{line-height:1.2}section{margin:36px 0}.content{white-space:normal}"
        "@media print{body{margin:0}}</style></head><body>"
        f"<h1>{html.escape(request.title)}</h1>{sections}"
        f'<script type="application/json" id="lesson-evidence">{payload}</script>'
        "</body></html>"
    )


def _minimal_pdf(title: str) -> bytes:
    # Deterministic, valid one-page PDF used by the local test/development engine.
    safe = re.sub(r"[^A-Za-z0-9 ._-]", "", title)[:80] or "Tutor Assistant material"
    stream = f"BT /F1 18 Tf 72 760 Td ({safe}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


class LocalDocumentEngine:
    name = "local-template"

    def build(self, request: DocumentBuildRequest) -> DocumentBuildResult:
        tex = _tex_source(request)
        web = _html_source(request)
        return DocumentBuildResult(
            engine=self.name,
            log="Local deterministic preview build",
            outputs=[
                DocumentOutput("tex", "material.tex", "application/x-tex", tex.encode()),
                DocumentOutput("html", "material.html", "text/html; charset=utf-8", web.encode()),
                DocumentOutput(
                    "pdf", "material.pdf", "application/pdf", _minimal_pdf(request.title)
                ),
            ],
        )


class LatexedDocumentEngine:
    name = "latex-for-everyone"

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        max_pdf_mb: int = 50,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport
        self.max_pdf_bytes = max_pdf_mb * 1024 * 1024
        self.circuit_breaker = circuit_breaker or CircuitBreaker("document-engine")

    def build(self, request: DocumentBuildRequest) -> DocumentBuildResult:
        tex = _tex_source(request)
        web = _html_source(request)
        headers = {"Authorization": f"Bearer {self.token}"}
        self.circuit_breaker.before_call()
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                response = client.post(
                    f"{self.base_url}/api/compile/raw",
                    headers=headers,
                    json={"content": tex, "files": {}},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") != "success" or not payload.get("pdf_url"):
                    raise DocumentEngineError(payload.get("error") or "LaTeX compilation failed")
                pdf_url = str(payload["pdf_url"])
                if pdf_url.startswith("/"):
                    pdf_url = f"{self.base_url}{pdf_url}"
                chunks: list[bytes] = []
                total = 0
                with client.stream("GET", pdf_url, headers=headers) as pdf_response:
                    pdf_response.raise_for_status()
                    for chunk in pdf_response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_pdf_bytes:
                            raise DocumentEngineError("compiled PDF exceeds configured size limit")
                        chunks.append(chunk)
                pdf_content = b"".join(chunks)
                if not pdf_content.startswith(b"%PDF-"):
                    raise DocumentEngineError("compiler returned a non-PDF artifact")
        except Exception as exc:
            self.circuit_breaker.record_failure(exc)
            if isinstance(exc, DocumentEngineError):
                raise
            raise DocumentEngineError(f"latex-for-everyone request failed: {exc}") from exc
        self.circuit_breaker.record_success()
        return DocumentBuildResult(
            engine=self.name,
            log=str(payload.get("output") or payload.get("compile_time") or "Compiled"),
            outputs=[
                DocumentOutput("tex", "material.tex", "application/x-tex", tex.encode()),
                DocumentOutput("html", "material.html", "text/html; charset=utf-8", web.encode()),
                DocumentOutput("pdf", "material.pdf", "application/pdf", pdf_content),
            ],
        )


class LocalArtifactStorage(ArtifactStorage):
    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        pure = PurePosixPath(key)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ValueError("invalid artifact storage key")
        candidate = self.root.joinpath(*pure.parts).resolve()
        if self.root not in candidate.parents:
            raise ValueError("artifact path escapes storage root")
        return candidate

    def put(self, key: str, content: bytes, media_type: str) -> StoredArtifact:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
        return StoredArtifact(
            key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            media_type=media_type,
        )

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()
