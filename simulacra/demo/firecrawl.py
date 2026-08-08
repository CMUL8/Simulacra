"""Optional Firecrawl document parse — PDF/DOCX/XLSX → markdown for extract.

Uses Firecrawl `/v2/parse` when FIRECRAWL_API_KEY is set. Without a key,
unsupported types stay inventory-only (honest skip).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

from simulacra.env import load_dotenv

log = logging.getLogger("simulacra.firecrawl")

FIRECRAWL_PARSE_URL = "https://api.firecrawl.dev/v2/parse"

# Types Firecrawl /parse supports that our native extract does not
FIRECRAWL_EXT = {
	".pdf",
	".docx",
	".doc",
	".xlsx",
	".xls",
	".pptx",
	".ppt",
	".odt",
	".rtf",
	".epub",
}


def firecrawl_enabled() -> bool:
	load_dotenv()
	return bool((os.environ.get("FIRECRAWL_API_KEY") or "").strip())


def firecrawl_api_key() -> str | None:
	load_dotenv()
	key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
	return key or None


def can_firecrawl_parse(path: Path) -> bool:
	return path.suffix.lower() in FIRECRAWL_EXT and firecrawl_enabled()


def parse_document_to_markdown(path: Path, *, timeout: float = 120.0) -> str | None:
	"""Upload local file bytes to Firecrawl /v2/parse; return markdown or None."""
	key = firecrawl_api_key()
	if not key:
		return None
	if not path.is_file():
		return None
	if path.suffix.lower() not in FIRECRAWL_EXT:
		return None

	data = path.read_bytes()
	if not data:
		return None

	boundary = f"----SimulacraBoundary{uuid.uuid4().hex}"
	filename = path.name
	ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
	options = {
		"formats": ["markdown"],
		"timeout": int(min(max(timeout * 1000, 10_000), 300_000)),
	}
	if path.suffix.lower() == ".pdf":
		options["parsers"] = [{"type": "pdf", "mode": "auto", "maxPages": 80}]

	parts: list[bytes] = []
	# options JSON part
	parts.append(f"--{boundary}\r\n".encode())
	parts.append(b'Content-Disposition: form-data; name="options"\r\n')
	parts.append(b"Content-Type: application/json\r\n\r\n")
	parts.append(json.dumps(options).encode() + b"\r\n")
	# file part
	parts.append(f"--{boundary}\r\n".encode())
	parts.append(
		f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
	)
	parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
	parts.append(data + b"\r\n")
	parts.append(f"--{boundary}--\r\n".encode())
	body = b"".join(parts)

	req = request.Request(
		FIRECRAWL_PARSE_URL,
		data=body,
		method="POST",
		headers={
			"Authorization": f"Bearer {key}",
			"Content-Type": f"multipart/form-data; boundary={boundary}",
			"Content-Length": str(len(body)),
		},
	)
	try:
		with request.urlopen(req, timeout=timeout) as resp:
			payload = json.loads(resp.read().decode("utf-8", errors="replace"))
	except error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")[:300]
		log.warning("firecrawl parse HTTP %s for %s: %s", exc.code, filename, detail)
		return None
	except Exception as exc:  # noqa: BLE001
		log.warning("firecrawl parse failed for %s: %s", filename, exc)
		return None

	return _markdown_from_payload(payload)


def _markdown_from_payload(payload: dict[str, Any]) -> str | None:
	if not payload:
		return None
	data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
	if not isinstance(data, dict):
		return None
	md = data.get("markdown")
	if isinstance(md, str) and md.strip():
		return md
	# Some responses nest document
	doc = data.get("data") if isinstance(data.get("data"), dict) else None
	if doc and isinstance(doc.get("markdown"), str) and doc["markdown"].strip():
		return doc["markdown"]
	return None


def parse_and_cache(project_id: str, path: Path, rel: str) -> Path | None:
	"""Parse via Firecrawl and cache markdown under work/parsed/. Returns md path."""
	from .runs import project_dir

	md = parse_document_to_markdown(path)
	if not md:
		return None
	out_dir = project_dir(project_id) / "work" / "parsed"
	out_dir.mkdir(parents=True, exist_ok=True)
	safe = rel.replace("/", "__")
	out = out_dir / f"{safe}.md"
	header = f"# Parsed from `{rel}` via Firecrawl\n\n"
	out.write_text(header + md)
	return out
