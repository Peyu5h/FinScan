import os
from typing import Type

import httpx
from crewai.tools import BaseTool
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pypdf import PdfReader

load_dotenv()

MAX_PDF_CHARS = 15000


class PDFPathInput(BaseModel):
    path: str = Field(default="data/sample.pdf", description="path to the pdf file")


# reads and extracts text from a financial pdf
class FinancialDocumentTool(BaseTool):
    name: str = "financial_document_reader"
    description: str = "Reads a PDF file and returns its text content."
    args_schema: Type[BaseModel] = PDFPathInput

    def _run(self, path: str = "data/sample.pdf") -> str:
        if not os.path.exists(path):
            return f"error: file not found at {path}"

        try:
            reader = PdfReader(path)
            chunks = []
            for page in reader.pages:
                text = page.extract_text() or ""
                while "\n\n\n" in text:
                    text = text.replace("\n\n\n", "\n\n")
                chunks.append(text)

            full_text = "\n".join(chunks)
            if not full_text.strip():
                return "error: pdf has no extractable text"

            total = len(full_text)
            print(f"[tools] read {len(reader.pages)} pages, {total} chars")

            # truncate large documents to stay within token limits
            if total > MAX_PDF_CHARS:
                half = MAX_PDF_CHARS // 2
                full_text = (
                    full_text[:half]
                    + f"\n\n[... truncated {total - MAX_PDF_CHARS} chars — "
                    + f"showing first and last sections of {total} total chars ...]\n\n"
                    + full_text[-half:]
                )
                print(f"[tools] truncated to ~{MAX_PDF_CHARS} chars")

            return full_text

        except Exception as e:
            return f"error: failed to read pdf - {e}"


class SearchInput(BaseModel):
    query: str = Field(..., description="search query")


# web search via serper.dev
class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Searches the web for current market data and financial news."
    args_schema: Type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:
        api_key = os.getenv("SERPER_API_KEY", "")
        if not api_key:
            return "web search unavailable (SERPER_API_KEY not set)"

        try:
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 5},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            lines = []
            for item in data.get("organic", [])[:5]:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                link = item.get("link", "")
                lines.append(f"- {title}: {snippet} ({link})")

            return "\n".join(lines) if lines else "no results found"

        except Exception as e:
            return f"search failed: {e}"


# tool instances used by agents
pdf_tool = FinancialDocumentTool()
search_tool = WebSearchTool()
