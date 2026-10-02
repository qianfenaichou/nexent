"""Unit tests for durable knowledge-base file lifecycle persistence."""

import contextlib
import importlib
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _install_storage_import_stubs() -> None:
    """Keep database tests independent from the SDK's optional model stack."""
    nexent_pkg = sys.modules.setdefault("nexent", types.ModuleType("nexent"))
    storage_pkg = sys.modules.setdefault("nexent.storage", types.ModuleType("nexent.storage"))
    storage_pkg.__path__ = []
    factory = sys.modules.setdefault(
        "nexent.storage.storage_client_factory",
        types.ModuleType("nexent.storage.storage_client_factory"),
    )
    factory.create_storage_client_from_config = lambda *_args, **_kwargs: MagicMock()
    factory.MinIOStorageConfig = type("MinIOStorageConfig", (), {})
    setattr(nexent_pkg, "storage", storage_pkg)
    setattr(storage_pkg, "storage_client_factory", factory)


_install_storage_import_stubs()
lifecycle_db = importlib.import_module("backend.database.knowledge_file_lifecycle_db")


def _session_context(session):
    @contextlib.contextmanager
    def _context():
        yield session

    return _context


def test_create_file_record_persists_uploading_row(monkeypatch):
    session = MagicMock()
    row = MagicMock(file_id="fid-1")
    monkeypatch.setattr(lifecycle_db, "KnowledgeFileLifecycle", MagicMock(return_value=row))
    monkeypatch.setattr(lifecycle_db, "as_dict", lambda value: {"file_id": value.file_id})
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    result = lifecycle_db.create_file_record(
        file_id="fid-1",
        tenant_id="tenant-1",
        knowledge_id=10,
        index_name="kb-1",
        original_filename="broken.pdf",
        object_name="knowledge_base/object.pdf",
    )

    assert result == {"file_id": "fid-1"}
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once_with()


def test_create_file_records_uses_one_transaction_for_the_whole_batch(monkeypatch):
    session = MagicMock()
    rows = [MagicMock(file_id="fid-1"), MagicMock(file_id="fid-2")]
    lifecycle_model = MagicMock(side_effect=rows)
    monkeypatch.setattr(lifecycle_db, "KnowledgeFileLifecycle", lifecycle_model)
    monkeypatch.setattr(
        lifecycle_db,
        "as_dict",
        lambda value: {"file_id": value.file_id},
    )
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    result = lifecycle_db.create_file_records([
        {
            "file_id": "fid-1",
            "tenant_id": "tenant-1",
            "knowledge_id": 10,
            "index_name": "kb-1",
            "original_filename": "a.pdf",
            "upload_owner_service": "nexent-config",
        },
        {
            "file_id": "fid-2",
            "tenant_id": "tenant-1",
            "knowledge_id": 10,
            "index_name": "kb-1",
            "original_filename": "b.pdf",
        },
    ])

    assert result == [{"file_id": "fid-1"}, {"file_id": "fid-2"}]
    session.add_all.assert_called_once_with(rows)
    session.add.assert_not_called()
    session.flush.assert_called_once_with()
    assert lifecycle_model.call_args_list[0].kwargs[
        "upload_owner_service"
    ] == "nexent-config"
    assert lifecycle_model.call_args_list[1].kwargs[
        "upload_owner_service"
    ] is None


def test_create_file_records_returns_empty_for_empty_batch(monkeypatch):
    """Empty uploads should not open a database transaction."""
    session = MagicMock()
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    assert lifecycle_db.create_file_records([]) == []
    session.add_all.assert_not_called()
    session.flush.assert_not_called()


def test_delete_tombstone_updates_existing_row(monkeypatch):
    existing = {"file_id": "fid-2", "status": "FAILED"}
    transition = MagicMock(return_value={"file_id": "fid-2", "status": "DELETE_REQUESTED"})
    monkeypatch.setattr(lifecycle_db, "get_file_record", MagicMock(return_value=existing))
    monkeypatch.setattr(lifecycle_db, "transition_file_record", transition)

    result = lifecycle_db.create_delete_tombstone(
        tenant_id="tenant-1",
        knowledge_id=10,
        index_name="kb-1",
        object_name="knowledge_base/object.pdf",
        requested_by="user-1",
    )

    assert result["status"] == "DELETE_REQUESTED"
    transition.assert_called_once()
    assert transition.call_args.kwargs["updated_by"] == "user-1"
    assert "delete_requested_at" not in transition.call_args.kwargs
    assert "delete_requested_by" not in transition.call_args.kwargs


def test_delete_tombstone_keeps_existing_deleted_row(monkeypatch):
    """Repeated legacy deletion must not transition an already hidden row."""
    existing = {"file_id": "fid-deleted", "status": "DELETED"}
    transition = MagicMock()
    monkeypatch.setattr(lifecycle_db, "get_file_record", MagicMock(return_value=existing))
    monkeypatch.setattr(lifecycle_db, "transition_file_record", transition)

    result = lifecycle_db.create_delete_tombstone(
        tenant_id="tenant-1",
        knowledge_id=10,
        index_name="kb-1",
        object_name="knowledge_base/object.pdf",
    )

    assert result == existing
    transition.assert_not_called()


def test_new_file_id_is_opaque_and_stable_length():
    file_id = lifecycle_db.new_file_id()
    assert len(file_id) == 32
    assert file_id.isalnum()


def test_get_file_record_by_id_applies_tenant_index_and_visibility_filters(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    row = MagicMock(file_id="fid-3")
    query.first.return_value = row
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(lifecycle_db, "as_dict", lambda value: {"file_id": value.file_id})

    result = lifecycle_db.get_file_record(
        file_id="fid-3",
        tenant_id="tenant-1",
        index_name="kb-1",
        include_hidden=False,
    )

    assert result == {"file_id": "fid-3"}
    assert query.filter.call_count == 4


def test_get_file_record_by_legacy_object_and_empty_lookup(monkeypatch):
    assert lifecycle_db.get_file_record() is None

    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.return_value = None
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    assert lifecycle_db.get_file_record(object_name="knowledge_base/a.txt", include_hidden=True) is None
    assert query.filter.call_count == 1


def test_list_file_records_applies_tenant_and_hides_deleted_rows(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = [MagicMock(file_id="fid-4")]
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(lifecycle_db, "as_dict", lambda value: {"file_id": value.file_id})

    assert lifecycle_db.list_file_records(index_name="kb-1", tenant_id="tenant-1") == [{"file_id": "fid-4"}]
    assert query.filter.call_count == 3

    query.filter.reset_mock()
    query.order_by.return_value.all.return_value = []
    assert lifecycle_db.list_file_records(index_name="kb-1", include_hidden=True) == []
    assert query.filter.call_count == 1


def test_fail_interrupted_file_tasks_reuses_failed_state(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    processing = types.SimpleNamespace(
        file_id="processing",
        status="PROCESSING",
        stage="PROCESS",
        process_task_id="process-1",
        forward_task_id=None,
        version=1,
    )
    forwarding = types.SimpleNamespace(
        file_id="forwarding",
        status="FORWARDING",
        stage="FORWARD",
        process_task_id="process-2",
        forward_task_id="forward-2",
        version=4,
    )
    query.all.return_value = [processing, forwarding]
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(
        lifecycle_db,
        "as_dict",
        lambda value: {
            "file_id": value.file_id,
            "process_task_id": value.process_task_id,
            "forward_task_id": value.forward_task_id,
        },
    )

    records = lifecycle_db.fail_interrupted_file_tasks()

    assert [record["file_id"] for record in records] == ["processing", "forwarding"]
    assert processing.status == "FAILED"
    assert processing.error_stage == "PROCESS"
    assert processing.version == 2
    assert forwarding.status == "FAILED"
    assert forwarding.error_stage == "FORWARD"
    assert forwarding.version == 5
    recovery_filter = query.filter.call_args.args[1]
    assert "forward_task_id IS NOT NULL" in str(recovery_filter)


def test_list_uploading_files_created_before_scopes_recovery_to_owner(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = [types.SimpleNamespace(file_id="uploading")]
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(lifecycle_db, "as_dict", lambda value: {"file_id": value.file_id})

    rows = lifecycle_db.list_uploading_files_created_before(
        datetime(2026, 9, 1),
        "nexent-config",
    )

    assert rows == [{"file_id": "uploading"}]
    query.order_by.assert_called_once()
    owner_filter = next(
        argument
        for argument in query.filter.call_args.args
        if getattr(argument.left, "name", None) == "upload_owner_service"
    )
    assert owner_filter.right.value == "nexent-config"


@pytest.mark.parametrize("upload_owner_service", [None, ""])
def test_list_uploading_files_created_before_rejects_missing_owner(
    monkeypatch,
    upload_owner_service,
):
    session = MagicMock()
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    with pytest.raises(ValueError, match="upload_owner_service is required"):
        lifecycle_db.list_uploading_files_created_before(
            datetime(2026, 9, 1),
            upload_owner_service,
        )

    session.query.assert_not_called()


def test_transition_file_record_updates_allowed_fields_and_version(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    row = MagicMock(file_id="fid-5", version=2)
    query.first.return_value = row
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(lifecycle_db, "as_dict", lambda value: {"file_id": value.file_id, "version": value.version})

    result = lifecycle_db.transition_file_record(
        "fid-5",
        status="FAILED",
        stage="PROCESS",
        expected_statuses=("PROCESSING",),
        expected_version=2,
        updated_by="user-1",
        original_filename="renamed.pdf",
        error_code="PARSE_FAILED",
        error_message="bad input",
        ignored_field="must not be assigned",
    )

    assert result == {"file_id": "fid-5", "version": 3}
    assert row.status == "FAILED"
    assert row.stage == "PROCESS"
    assert row.error_code == "PARSE_FAILED"
    assert row.error_message == "bad input"
    assert row.updated_by == "user-1"
    assert row.original_filename == "renamed.pdf"
    assert "ignored_field" not in row.__dict__
    session.flush.assert_called_once()


def test_transition_file_record_returns_none_for_stale_row(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = None
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))
    monkeypatch.setattr(
        lifecycle_db,
        "as_dict",
        lambda value: {"file_id": value.file_id, "version": value.version},
    )

    assert lifecycle_db.transition_file_record("missing", expected_version=9) is None
    session.flush.assert_not_called()

    row = MagicMock(file_id="fid-7", version=0)
    query.first.return_value = row
    assert lifecycle_db.transition_file_record("fid-7") == {"file_id": "fid-7", "version": 1}
    assert row.version == 1


def test_delete_file_record_physically_removes_matching_row(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.delete.return_value = 1
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    assert lifecycle_db.delete_file_record(
        "fid-delete",
        expected_statuses=("DELETE_REQUESTED", "DELETED"),
    ) is True
    query.delete.assert_called_once_with(synchronize_session=False)


def test_delete_file_record_is_idempotent_when_row_is_missing(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.delete.return_value = 0
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    assert lifecycle_db.delete_file_record("fid-missing") is False


def test_delete_file_records_for_knowledge_base_only_removes_eligible_statuses(monkeypatch):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.delete.return_value = 3
    monkeypatch.setattr(lifecycle_db, "get_db_session", _session_context(session))

    assert lifecycle_db.delete_file_records_for_knowledge_base(
        index_name="kb-1",
        tenant_id="tenant-1",
        knowledge_id=10,
    ) == 3
    assert query.filter.call_count == 3
    query.delete.assert_called_once_with(synchronize_session=False)


def test_delete_tombstone_creates_and_finalizes_missing_row(monkeypatch):
    monkeypatch.setattr(lifecycle_db, "get_file_record", MagicMock(return_value=None))
    created = {"file_id": "fid-6", "status": "DELETE_REQUESTED"}
    monkeypatch.setattr(lifecycle_db, "create_file_record", MagicMock(return_value=created))
    transition = MagicMock(return_value={"file_id": "fid-6", "status": "DELETE_REQUESTED"})
    monkeypatch.setattr(lifecycle_db, "transition_file_record", transition)

    result = lifecycle_db.create_delete_tombstone(
        tenant_id="tenant-1",
        knowledge_id=10,
        index_name="kb-1",
        object_name="knowledge_base/missing.txt",
        original_filename="missing.txt",
        requested_by="user-1",
    )

    assert result == {"file_id": "fid-6", "status": "DELETE_REQUESTED"}
    assert lifecycle_db.create_file_record.call_args.kwargs["status"] == "DELETE_REQUESTED"
    transition.assert_called_once()
    assert transition.call_args.args == ("fid-6",)
    assert "deleted_at" not in transition.call_args.kwargs

# --- The doubles installed above are scoped to this module --------------------------
# Registering fakes in ``sys.modules`` is what lets the modules under test import
# without their real collaborators, but a hand-built ``types.ModuleType`` has no
# ``__file__`` and no package ``__path__``. Left in place it makes every later
# ``from <package>.<module> import ...`` in the same run fail with
# "ModuleNotFoundError: ... '<package>' is not a package", so the registrations are
# dropped again as soon as this module finishes importing. ``setup_module`` replays
# them for this module's own tests, which keeps patching by module path working, and
# ``teardown_module`` takes them back out. Entries that were already present are
# restored to their previous value rather than deleted.

import types as _scope_types
from unittest.mock import Mock as _scope_mock_base


def _is_import_double(module):
    """Tell a hand-made test double from a genuinely imported module."""
    if isinstance(module, _scope_mock_base):
        return True
    if type(module) is not _scope_types.ModuleType:
        return False
    if getattr(module, "__file__", None):
        return False
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    return not origin or origin in ("namespace", "built-in", "frozen")


def _collect_import_doubles(snapshot):
    """Map every double this module installed to the entry it displaced."""
    doubles = {}
    for name, module in list(sys.modules.items()):
        if snapshot.get(name) is module:
            continue
        if _is_import_double(module):
            doubles[name] = (snapshot.get(name), module)
    return doubles


_IMPORT_DOUBLES = _collect_import_doubles(_STUB_MODULE_SNAPSHOT)
_SAVED_IMPORT_MODULES = {}


def _install_import_doubles():
    """Register this module's doubles, returning what has to be put back."""
    saved = {}
    for name, (_previous, double) in _IMPORT_DOUBLES.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = double
    return saved


def _restore_modules(saved):
    """Undo :func:`_install_import_doubles`, deleting entries that were absent."""
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _eject_import_doubles():
    """Take the doubles out again once this module has finished importing."""
    for name, (previous, _double) in _IMPORT_DOUBLES.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def setup_module(_module=None):
    """Re-register the doubles for the duration of this module's tests."""
    global _SAVED_IMPORT_MODULES
    _SAVED_IMPORT_MODULES = _install_import_doubles()


def teardown_module(_module=None):
    """Drop the doubles again so the next module sees the real packages."""
    _restore_modules(_SAVED_IMPORT_MODULES)


_eject_import_doubles()
