"""
ingest_assets service (T-02) - registry parsing, doc_asset_t registration,
and the native Nexent ingestion client.

Layered per the pipeline/ contract: the CLI (ingest_assets.py) owns argument
parsing and exit codes; this module owns behavior and is what the tests
exercise. The HTTP client targets the native chain verified against source:

- POST /api/file/upload          (file_management_app.py L115, multipart)
- POST /api/file/process         (file_management_app.py L207, JSON body)
- POST /api/indices/{index_name} (vectordatabase_app.py L87, index create)
- POST /api/indices/{index_name}/documents (L543, doc indexing)
- POST /api/indices/search/hybrid         (L1068, retrieval smoke)

The vectordatabase router mounts at prefix "/indices" under root_path
"/api" (config_app.py L127), so the CLI hits the web gateway as
/api/indices/... - the router prefix differs from the module filename.
"""
import csv
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Column contract for registry.csv. Extra columns are preserved into
# meta_data but never required; source_url and license_note may be empty
# for offline-provided files (recorded honestly as null, never invented).
REGISTRY_COLUMNS = [
    "asset_no", "title", "doc_type", "modality", "authority_level",
    "source_url", "license_note", "local_file", "split",
]

DOC_TYPES = {
    "guideline", "drug_label", "lab_report", "policy", "material_list",
    "edu_graphic",
}
MODALITIES = {"text", "table", "image_text"}
AUTHORITY_LEVELS = {1, 2, 3, 4}
SPLITS = {"build", "blind"}


@dataclass
class RegistryRow:
    """One registry.csv line after validation."""
    asset_no: str
    title: str
    doc_type: str
    modality: str
    authority_level: int
    source_url: str | None
    license_note: str | None
    local_file: str
    split: str
    row_number: int = 0
    errors: list[str] = field(default_factory=list)

    def to_doc_asset_values(self, tenant_id: str) -> dict[str, Any]:
        """Column mapping into doc_asset_t (knowevo_db.py DocAsset)."""
        return {
            "tenant_id": tenant_id,
            "asset_no": self.asset_no,
            "title": self.title,
            "doc_type": self.doc_type,
            "modality": self.modality,
            "authority_level": self.authority_level,
            "source_url": self.source_url or None,
            "source_note": self.license_note or None,
            "parse_status": "pending",
            "meta_data": {
                "split": self.split,
                "row_number": self.row_number,
            },
        }


def _clean(value: str | None) -> str:
    return (value or "").strip()


def parse_registry(path: Path) -> list[RegistryRow]:
    """Parse and validate registry.csv.

    Rows with structural errors (bad doc_type/modality/authority_level/
    split, missing asset_no or title) keep their slot with errors filled,
    so the caller can report a full rejection list instead of aborting on
    the first bad line. Missing source_url is NOT an error here (offline
    files); the compliance report layer surfaces it separately.
    """
    rows: list[RegistryRow] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing_cols = [c for c in
                        ("asset_no", "title", "doc_type", "modality")
                        if c not in header]
        if missing_cols:
            raise ValueError(f"registry missing required columns: {missing_cols}")
        for i, raw in enumerate(reader, start=2):  # header is line 1
            row = RegistryRow(
                asset_no=_clean(raw.get("asset_no")),
                title=_clean(raw.get("title")),
                doc_type=_clean(raw.get("doc_type")),
                modality=_clean(raw.get("modality") or "text"),
                authority_level=0,
                source_url=_clean(raw.get("source_url")) or None,
                license_note=_clean(raw.get("license_note")) or None,
                local_file=_clean(raw.get("local_file")),
                split=_clean(raw.get("split") or "build"),
                row_number=i,
            )
            try:
                row.authority_level = int(_clean(raw.get("authority_level") or "3"))
            except ValueError:
                row.errors.append("authority_level not an integer")

            if not row.asset_no:
                row.errors.append("asset_no empty")
            if not row.title:
                row.errors.append("title empty")
            if row.doc_type not in DOC_TYPES:
                row.errors.append(f"doc_type '{row.doc_type}' not in {sorted(DOC_TYPES)}")
            if row.modality not in MODALITIES:
                row.errors.append(f"modality '{row.modality}' not in {sorted(MODALITIES)}")
            if row.authority_level not in AUTHORITY_LEVELS:
                row.errors.append(f"authority_level {row.authority_level} not in 1-4")
            if row.split not in SPLITS:
                row.errors.append(f"split '{row.split}' not in {sorted(SPLITS)}")
            if not row.local_file:
                row.errors.append("local_file empty (no corpus file to ingest)")
            rows.append(row)
    return rows


def build_supersede_link(rows: list[RegistryRow], tenant_id: str,
                         lookup: dict[str, str]) -> list[dict[str, Any]]:
    """Version lineage: pair each newer guide with the asset_no of the
    older edition it supersedes (2020 -> 2024 anchor pair for T-11).

    lookup: {asset_no: doc_asset_t.id} from a prior registration pass.
    Returns update dicts [{"id":..., "supersede_of":...}] the caller
    applies in one session.
    """
    updates: list[dict[str, Any]] = []
    edition_re = re.compile(r"(19|20)\d{2}")
    guides = [r for r in rows if r.doc_type == "guideline"]
    for newer in guides:
        if not (newer.source_url or newer.local_file):
            continue
        m_new = edition_re.search(newer.title)
        if not m_new:
            continue
        new_year = int(m_new.group(0))
        # Same title family: shared prefix up to the edition year. The CDS
        # 2024 edition dropped "2型" from the title, so the family key is
        # the text before the year, minus the "2型" marker (e.g. both
        # "中国2型糖尿病防治指南(2020" and "中国糖尿病防治指南(2024"
        # normalize to 中国糖尿病防治指南).
        family = re.sub(r"2型|（2型", "", newer.title[:m_new.start()])
        candidates = []
        for r in guides:
            if r is newer:
                continue
            m_old = edition_re.search(r.title)
            if not m_old or int(m_old.group(0)) >= new_year:
                continue
            r_family = re.sub(r"2型|（2型", "", r.title[:m_old.start()])
            if r_family == family:
                candidates.append(r)
        if not candidates:
            continue
        older = max(candidates,
                    key=lambda r: int(edition_re.search(r.title).group(0)))
        older_id = lookup.get(older.asset_no)
        newer_id = lookup.get(newer.asset_no)
        if older_id and newer_id:
            updates.append({"id": newer_id, "supersede_of": older_id})
    return updates


def parse_quality_score(source_text: str) -> float:
    """Parse health check score in [0, 1] from an extracted text artifact.

    Signals (each additive, capped at 1.0; any content is better than none):
    - empty text -> 0.0
    - length: >=800 chars is full score for this axis
    - structure: headings / list markers / tables present
    - density: CJK or latin word ratio in a sample
    """
    if not source_text or not source_text.strip():
        return 0.0
    text = source_text.strip()
    score = 0.0
    if len(text) >= 800:
        score += 0.4
    elif len(text) >= 200:
        score += 0.2
    elif len(text) > 0:
        score += 0.1
    if re.search(r"^#+\s|第[一二三四五六七八九十百]+[章节篇]|^\s*[\d一二三四五六七八九十]+[、.．]\s",
                 text, re.MULTILINE):
        score += 0.2  # headings survived
    if re.search(r"^[-*•]|\|\s*-{2,}\s*\|", text, re.MULTILINE):
        score += 0.2  # lists or table rows survived
    sample = text[:500]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", sample))
    if cjk >= 20 or latin_words >= 15:
        score += 0.2
    return round(min(score, 1.0), 2)


@dataclass
class NativeIngestClient:
    """Thin HTTP client for the native upload -> process -> index chain.

    transport: callable(method, url, **kwargs) -> (status_code, body_dict)
    injected so tests run without a live stack; the CLI binds it to
    requests.Session.request. Cookie/session handling belongs to the CLI
    (login step), this class only replays the verified native endpoints.
    """
    base_url: str
    transport: Any
    auth_headers: dict[str, str] = field(default_factory=dict)

    def _call(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        status, body = self.transport(method, url, **kwargs)
        if isinstance(body, (bytes, str)):
            body = _safe_json(body)
        if status >= 400:
            raise RuntimeError(f"HTTP {status} on {url}: "
                               f"{str(body)[:200]}")
        return body or {}

    def create_index(self, index_name: str, embedding_model_id: int | None = None):
        """POST /api/indices/{index_name} (index create; embedding_model_id
        optional body field per vectordatabase_app.py L110)."""
        body: dict[str, Any] = {}
        if embedding_model_id is not None:
            body["embedding_model_id"] = embedding_model_id
        return self._call("POST", f"{self.base_url}/api/indices/{index_name}",
                          json=body)

    def upload_file(self, file_path: Path, destination: str = "local",
                    folder: str = "attachments") -> dict[str, Any]:
        """POST /api/file/upload (multipart, file_management_app.py L115)."""
        with open(file_path, "rb") as fh:
            files = {"file": (file_path.name, fh)}
            data = {"destination": destination, "folder": folder}
            return self._call("POST", f"{self.base_url}/api/file/upload",
                              files=files, data=data)

    def process_files(self, files: list[dict[str, str]], index_name: str,
                      destination: str = "local",
                      chunking_strategy: str = "basic") -> dict[str, Any]:
        """POST /api/file/process (file_management_app.py L207)."""
        return self._call("POST", f"{self.base_url}/api/file/process", json={
            "files": files, "index_name": index_name,
            "destination": destination,
            "chunking_strategy": chunking_strategy,
        })

    def index_documents(self, index_name: str, data: list[dict[str, Any]]):
        """POST /api/indices/{index_name}/documents (L543)."""
        return self._call("POST",
                          f"{self.base_url}/api/indices/{index_name}/documents",
                          json=data)

    def hybrid_search(self, query: str, index_names: list[str],
                      top_k: int = 5) -> dict[str, Any]:
        """POST /api/indices/search/hybrid (L1068, HybridSearchRequest)."""
        return self._call("POST", f"{self.base_url}/api/indices/search/hybrid",
                          json={"index_names": index_names, "query": query,
                                "top_k": top_k})


def _safe_json(body: Any) -> Any:
    import json
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return {"raw": body[:200]}
    return body


def register_assets(rows: list[RegistryRow], tenant_id: str,
                    session_factory=None) -> dict[str, Any]:
    """Idempotent bulk registration into doc_asset_t.

    An (tenant_id, asset_no) row that already exists is skipped (natural key
    uq_doc_asset_tenant_asset_no), so re-running the CLI never duplicates.
    Returns {registered: n, skipped: n, ids: {asset_no: row_id}, errors: []}.
    """
    from database.knowevo_db import DocAsset, _get_db_session

    if session_factory is None:
        session_factory = _get_db_session
    ids: dict[str, str] = {}
    registered = skipped = 0
    errors: list[str] = []
    with session_factory() as session:
        existing = {
            r.asset_no: r.id for r in session.query(DocAsset)
            .filter(DocAsset.tenant_id == tenant_id)
            .filter(DocAsset.asset_no.in_([r.asset_no for r in rows]))
            .all()
        }
        for row in rows:
            if row.errors:
                errors.append(f"row {row.row_number} ({row.asset_no}): "
                             f"{'; '.join(row.errors)}")
                continue
            if row.asset_no in existing:
                ids[row.asset_no] = existing[row.asset_no]
                skipped += 1
                continue
            asset = DocAsset(**row.to_doc_asset_values(tenant_id))
            session.add(asset)
            session.flush()
            ids[row.asset_no] = asset.id
            registered += 1
    return {"registered": registered, "skipped": skipped,
            "ids": ids, "errors": errors}


def update_parse_result(tenant_id: str, asset_no: str,
                        parse_status: str, parse_quality: float | None,
                        session_factory=None) -> bool:
    """Write back parse_status / parse_quality for one asset (idempotent)."""
    from database.knowevo_db import DocAsset, _get_db_session

    if session_factory is None:
        session_factory = _get_db_session
    with session_factory() as session:
        row = session.query(DocAsset).filter(
            DocAsset.tenant_id == tenant_id,
            DocAsset.asset_no == asset_no,
        ).first()
        if row is None:
            return False
        row.parse_status = parse_status
        if parse_quality is not None:
            row.parse_quality = parse_quality
        session.flush()
    return True


def _as_uuid(value: Any) -> Any:
    """Coerce str-or-UUID into UUID; passes None through for filters."""
    if isinstance(value, uuid.UUID) or value is None:
        return value
    return uuid.UUID(str(value))


def apply_lineage(updates: list[dict[str, Any]], session_factory=None) -> int:
    """Apply supersede_of lineage updates from build_supersede_link."""
    from database.knowevo_db import DocAsset, _get_db_session

    if session_factory is None:
        session_factory = _get_db_session
    applied = 0
    with session_factory() as session:
        for upd in updates:
            row = session.query(DocAsset).filter(
                DocAsset.id == _as_uuid(upd["id"])
            ).first()
            if row is not None:
                row.supersede_of = _as_uuid(upd["supersede_of"])
                applied += 1
        session.flush()
    return applied


def corpus_file_digest(path: Path) -> str:
    """SHA-256 of a corpus file (provenance evidence in ingest_manifest)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
