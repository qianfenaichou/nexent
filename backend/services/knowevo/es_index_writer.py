"""Production ES write path for knowevo ingestion (T-08 follow-up).

PG is the graph of record; this module is the *projection* that makes the
entity acceptance index real: after an ingest run lands entities in
``kg_entity_t``, a best-effort bulk upsert reconciles them into the
production entity index so ``es_raw_list`` / the ES-first lookup can hit
real data instead of a probe-built ``_m2`` index.

Honest boundaries (all audited, never silently skipped):

- **Analyzer / IK**: ``GET _cat/plugins`` is probed once for
  ``analysis-ik``; the result lands in ``EsIndexWriter.audit``. Three
  audited outcomes: ``present`` (IK analyzers used), ``absent``
  (reachable server, no IK -> standard analyzer), ``error`` (any probe
  failure -> standard analyzer; a probe failure must never block
  ingestion). As of 2026-09-30 the ES container carries no IK plugin
  (``_cat/plugins`` is empty), so this round ships the standard analyzer;
  installing the plugin is a container change outside this module.
- **Dense write-back**: NOT implemented. The embedding model is not
  resolvable on the ingestion path (no KB config / model gateway reaches
  the ingest CLI), so no vector is ever written - and the strict mapping
  declares no ``embedding`` field at all, so a knn route cannot pretend
  to exist. ``KgEntity.embedding`` (PG JSONB) stays PG-only this round;
  the reason is a frozen constant (``DENSE_WRITEBACK_REASON``).
- **Index creation**: ``ensure_indices`` creates the production indices
  only when absent, with an explicit strict mapping (same shape as the
  M2 acceptance probes). Existing indices are never touched - unlike the
  M2 probes there is no destructive ``DELETE`` here.
- **Asset index**: the name constant + strict mapping exist and are
  created by ``ensure_indices``, but no document write path for
  ``doc_asset_t`` projections is implemented this round (the entities
  reconcile is the write path that exists).

Transport is the platform's ``ElasticSearchCore`` (duck-typed so offline
tests inject a fake): ``client.indices.exists/create``, ``client.bulk``
and ``client.cat.plugins``. Environment (read here on purpose -
``backend/consts/const.py`` is a shared wiring file this module must not
touch; names are the platform's existing ES env names):

- ``ELASTICSEARCH_HOST`` / ``ELASTICSEARCH_API_KEY``: ES credentials;
- ``KW_ES_SYNC_ON_INGEST``: explicit opt-in for the ingest-hook sync
  (``sync_tenant_entities``). Default OFF - the test harness sets
  ``ELASTICSEARCH_HOST`` globally (``test/conftest.py``), so host
  presence alone must never arm a write path; every existing ingest
  caller keeps today's behaviour bit for bit until the flag is set.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: Production index names (the M2 acceptance probes used ``*_m2`` variants;
#: these are the ones the production write path owns).
ENTITY_INDEX = "knowevo_entities"
ASSET_INDEX = "knowevo_assets"

ENV_ES_HOST = "ELASTICSEARCH_HOST"
ENV_ES_API_KEY = "ELASTICSEARCH_API_KEY"
ENV_SYNC_ON_INGEST = "KW_ES_SYNC_ON_INGEST"

#: Audit string for the honest dense-slot emptiness (see module docstring).
DENSE_WRITEBACK_REASON = "embedding_model_not_resolvable_at_ingestion_time"

IK_PLUGIN = "analysis-ik"
IK_INDEX_ANALYZER = "ik_max_word"
IK_SEARCH_ANALYZER = "ik_smart"


def _text_field(ik_enabled: bool) -> dict[str, Any]:
    """One BM25 text field; analyzers only ever appear when IK is real."""
    if not ik_enabled:
        return {"type": "text"}
    return {"type": "text", "analyzer": IK_INDEX_ANALYZER,
            "search_analyzer": IK_SEARCH_ANALYZER}


def entity_mapping(ik_enabled: bool = False) -> dict[str, Any]:
    """Explicit strict mapping for the production entity index.

    Same shape as the M2 acceptance probe (``entity_lookup_es_integration``):
    ``name``/``aliases.alias`` are the BM25 search surface, everything else
    is keyword, ``props`` is ``enabled: false`` (pass-through only - the
    dynamic mapper would otherwise guess numeric/date sub-fields and blow
    up shards, pitfall #153 family). No ``embedding`` field: there is no
    dense write-back to index vectors for (see module docstring).
    """
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "tenant_id": {"type": "keyword"},
                "stable_id": {"type": "keyword"},
                "name": _text_field(ik_enabled),
                "class_ref": {"type": "keyword"},
                "status": {"type": "keyword"},
                "aliases": {
                    "type": "object",
                    "properties": {
                        "alias": _text_field(ik_enabled),
                        "type": {"type": "keyword"},
                    },
                },
                "props": {"type": "object", "enabled": False},
            },
        },
    }


def asset_mapping(ik_enabled: bool = False) -> dict[str, Any]:
    """Explicit strict mapping for the production asset index.

    The M2 asset probe shape (``asset_search_m2_real_es``), promoted to the
    production name: ``title`` is the BM25 surface, filter dimensions are
    keyword/integer/boolean, and ``metadata`` keeps explicit sub-field
    types with a dynamic pass-through (a freely-guessed ``date`` under
    ``metadata.*`` exploded shards on 2026-09-29 - kept explicit here).
    Nothing writes documents into this index yet (see module docstring).
    """
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "tenant_id": {"type": "keyword"},
                "asset_no": {"type": "keyword"},
                "title": _text_field(ik_enabled),
                "doc_type": {"type": "keyword"},
                "modality": {"type": "keyword"},
                "authority_level": {"type": "integer"},
                "parse_status": {"type": "keyword"},
                "parse_quality": {"type": "float"},
                "superseded": {"type": "boolean"},
                "supersede_of": {"type": "keyword"},
                "metadata": {
                    "type": "object", "dynamic": True,
                    "properties": {
                        "split": _text_field(ik_enabled),
                        "row_number": {"type": "integer"},
                        "published_at": {"type": "keyword"},
                    },
                },
            },
        },
    }


def _alias_entries(raw: Any) -> list[dict[str, Any]]:
    """Normalize alias entries to the strict mapping shape.

    PG rows carry ``[{"alias": ..., "type": ...}]``; entity cards coming
    back from ES adapters carry plain ``[str]``. Both are accepted; empty
    or non-string aliases are dropped (never invented).
    """
    out: list[dict[str, Any]] = []
    for a in raw or []:
        if isinstance(a, Mapping):
            alias, typ = a.get("alias"), a.get("type")
        elif isinstance(a, str):
            alias, typ = a, None
        else:
            continue
        if not isinstance(alias, str) or not alias.strip():
            continue
        entry: dict[str, Any] = {"alias": alias}
        if isinstance(typ, str) and typ.strip():
            entry["type"] = typ
        out.append(entry)
    return out


class EsIndexWriter:
    """Bulk upsert of knowevo entities into the production ES index.

    ``core`` is duck-typed (platform ``ElasticSearchCore`` or a fake):
    only ``client.indices``, ``client.bulk`` and ``client.cat.plugins``
    are consumed. ``ik_enabled`` short-circuits the ``_cat/plugins``
    probe (wiring that already knows the answer); None means probe on
    first use and cache the audited result.
    """

    def __init__(self, core: Any,
                 entity_index: str = ENTITY_INDEX,
                 asset_index: str = ASSET_INDEX,
                 ik_enabled: bool | None = None):
        self.core = core
        self.entity_index = entity_index
        self.asset_index = asset_index
        self._ik_injected = ik_enabled
        self.audit: dict[str, Any] = {
            "ik_probe": None,       # present | absent | error | injected
            "ik_enabled": None,     # resolved bool
            "dense_writeback": DENSE_WRITEBACK_REASON,
            "indices_created": [],
            "last_bulk": None,
        }

    def probe_ik(self) -> bool:
        """One ``GET _cat/plugins`` for the IK analyzer plugin.

        Never raises: any failure degrades to the standard analyzer and is
        audited as ``error`` (an unreachable ES here must not break index
        creation, let alone ingestion). Result is cached in ``audit``.
        """
        if self._ik_injected is not None:
            self.audit["ik_probe"] = "injected"
            self.audit["ik_enabled"] = self._ik_injected
            return self._ik_injected
        try:
            plugins = self.core.client.cat.plugins(format="json") or []
            names = [str(p.get("component", ""))
                     for p in plugins if isinstance(p, Mapping)]
            has_ik = any(n == IK_PLUGIN or n.startswith(f"{IK_PLUGIN}-")
                         for n in names)
            self.audit["ik_probe"] = "present" if has_ik else "absent"
        except Exception as e:  # noqa: BLE001 - isolation is the contract
            logger.debug("IK plugin probe failed, falling back to standard "
                         "analyzer: %s", e)
            self.audit["ik_probe"] = "error"
            has_ik = False
        self.audit["ik_enabled"] = has_ik
        return has_ik

    def _resolve_ik(self) -> bool:
        if self.audit["ik_enabled"] is None:
            return self.probe_ik()
        return bool(self.audit["ik_enabled"])

    def ensure_indices(self) -> list[str]:
        """Idempotent creation of the production indices.

        An index that already exists is left untouched (no mapping change,
        no delete); only absent indices are created with the explicit
        strict mapping. Returns the names created by this call.
        """
        created: list[str] = []
        ik = self._resolve_ik()
        for name, mapping in ((self.entity_index, entity_mapping(ik)),
                              (self.asset_index, asset_mapping(ik))):
            if self.core.client.indices.exists(index=name):
                continue
            self.core.client.indices.create(
                index=name,
                settings=mapping["settings"],
                mappings=mapping["mappings"])
            created.append(name)
            self.audit["indices_created"].append(name)
        return created

    def upsert_entities(self, tenant_id: str, cards: list[dict[str, Any]],
                        refresh: bool | str = "wait_for") -> dict[str, Any]:
        """Bulk upsert entity cards, idempotent by (tenant, stable_id).

        ES document ids are ``f"{tenant_id}::{stable_id}"``: ``stable_id``
        is content-derived (``{class}:{name-key}``), so two tenants hold
        the same stable_id for the same real-world entity while sharing
        one index - the tenant prefix is what makes re-runs (and the
        full-tenant reconcile) idempotent instead of cross-tenant
        overwrites. Cards without a usable ``stable_id``/``name`` are
        dropped (the index never invents an entity); ``tenant_id`` is
        forced from the argument, never taken from the card.
        """
        ops: list[dict[str, Any]] = []
        docs = 0
        for card in cards or []:
            if not isinstance(card, Mapping):
                continue
            sid = card.get("stable_id")
            name = card.get("name")
            if not isinstance(sid, str) or not sid:
                continue
            if not isinstance(name, str) or not name:
                continue
            ops.append({"index": {
                "_index": self.entity_index,
                "_id": f"{tenant_id}::{sid}",
            }})
            ops.append({
                "tenant_id": tenant_id,
                "stable_id": sid,
                "name": name,
                "class_ref": card.get("class_ref") or "Unknown",
                "status": card.get("status") or "active",
                "aliases": _alias_entries(card.get("aliases")),
                "props": dict(card.get("props") or {}),
            })
            docs += 1
        if not ops:
            result = {"sent": 0, "bulk_errors": 0, "took_ms": None}
            self.audit["last_bulk"] = result
            return result
        response = self.core.client.bulk(operations=ops, refresh=refresh)
        items = (response or {}).get("items") or []
        errors = sum(1 for it in items
                     if isinstance(it, Mapping)
                     and (it.get("index") or {}).get("error"))
        result = {
            "sent": docs,
            "bulk_errors": errors,
            "took_ms": (response or {}).get("took"),
        }
        self.audit["last_bulk"] = result
        return result


def collect_active_entities(tenant_id: str,
                            session_factory=None) -> list[dict[str, Any]]:
    """Read one tenant's active entities from PG, shaped for upsert.

    Reconcile semantics: the whole active set, not just the run's delta -
    idempotent by (tenant, stable_id) and self-healing for prior partial
    runs. Exported fields are exactly the strict mapping's; ``embedding``
    is deliberately NOT exported (no dense write-back, see module
    docstring) and ``id`` is deliberately not exported (document ids are
    derived from tenant + stable_id).
    """
    from database.knowevo_db import KgEntity, _get_db_session

    if session_factory is None:
        session_factory = _get_db_session
    with session_factory() as session:
        rows = (
            session.query(KgEntity)
            .filter(KgEntity.tenant_id == tenant_id,
                    KgEntity.status == "active")
            .order_by(KgEntity.stable_id)
            .all()
        )
        return [
            {
                "stable_id": r.stable_id,
                "name": r.name,
                "class_ref": r.class_ref,
                "status": r.status,
                "aliases": list(r.aliases or []),
                "props": dict(r.props or {}),
            }
            for r in rows
        ]


def sync_tenant_entities(tenant_id: str, writer: EsIndexWriter | None = None,
                         session_factory=None) -> dict[str, Any] | None:
    """Env-gated best-effort reconcile of one tenant's entities into ES.

    Disabled (returns ``None``, zero side effects) unless
    ``KW_ES_SYNC_ON_INGEST`` is truthy AND the ES host env is present -
    see the module docstring for why the opt-in flag exists on top of the
    host check. Exceptions propagate to the caller: the ingest hook wraps
    this in a debug-log-only contract so ES can never affect ingestion.
    """
    writer = writer or build_es_index_writer()
    if writer is None:
        return None
    docs = collect_active_entities(tenant_id, session_factory)
    writer.ensure_indices()
    result = writer.upsert_entities(tenant_id, docs)
    result["ik_probe"] = writer.audit["ik_probe"]
    result["indices_created"] = list(writer.audit["indices_created"])
    return result


def sync_enabled() -> bool:
    """True only when ``KW_ES_SYNC_ON_INGEST`` is explicitly truthy."""
    return os.environ.get(ENV_SYNC_ON_INGEST, "").strip().lower() in (
        "1", "true", "yes", "on")


def build_es_index_writer(core: Any = None) -> EsIndexWriter | None:
    """Env-gated factory; ``None`` means "sync not armed, keep today".

    ``core`` injection is for tests (and callers that already hold an ES
    core); without it the SDK core is constructed lazily from the env so
    importing this module never pulls the ES client stack. A configured
    client does not connect at construction - connection failures surface
    at call time and belong to the caller's best-effort handling.
    """
    if not sync_enabled():
        return None
    host = os.environ.get(ENV_ES_HOST)
    if not host:
        return None
    if core is None:
        from nexent.vector_database.elasticsearch_core import ElasticSearchCore

        core = ElasticSearchCore(host=host,
                                 api_key=os.environ.get(ENV_ES_API_KEY))
    return EsIndexWriter(core=core)
