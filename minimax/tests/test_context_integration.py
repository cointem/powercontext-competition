"""Actual seekdb checks for updating, deleting and isolating engineering sources."""

import shutil
from pathlib import Path
import uuid

import pytest

from sdd_context import Materials


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "workspace"
    shutil.copytree(Path(__file__).parents[1] / "sample", root)
    shutil.copyfile(root / "before/orders.py", root / "orders.py")
    materials = Materials()
    project = "test-" + uuid.uuid4().hex
    materials.ingest(root, project)
    yield materials, root, project
    with materials.connection.cursor() as cursor:
        cursor.execute("DELETE FROM sdd_materials WHERE project_id=%s", (project,))
    materials.connection.commit()
    materials.close()


def test_retrieve_real_sources_and_isolate_projects(indexed):
    materials, root, project = indexed
    result = materials.retrieve(project, "list_orders", root)
    assert {
        "orders.py",
        "specs/orders.md",
        "tests/test_orders_feature.py",
        "tests/test_orders_regression.py",
    } <= {r["path"] for r in result["sources"]}
    assert materials.retrieve(project + "-other", "list_orders", root)["sources"] == []
    assert materials.retrieve(project, "nonexistent_term_987654", root)["sources"] == []
    for row in result["sources"]:
        assert row["body"] == (root / row["path"]).read_text()


def test_changed_spec_rejects_stale_index_and_refreshes(indexed):
    materials, root, project = indexed
    spec = root / "specs/orders.md"
    spec.write_text(
        spec.read_text() + "\nunique_spec_update_marker: allow refunded status.\n"
    )
    with pytest.raises(ValueError, match="Stale source"):
        materials.retrieve(project, "list_orders", root)
    materials.ingest(root, project)
    rows = materials.retrieve(project, "unique_spec_update_marker", root)["sources"]
    assert len(rows) == 1 and rows[0]["path"] == "specs/orders.md"
    assert "allow refunded status" in rows[0]["body"]


def test_deleted_source_removed_on_reimport(indexed):
    materials, root, project = indexed
    assert materials.retrieve(project, "inventory", root)["sources"]
    (root / "specs/inventory.md").unlink()
    materials.ingest(root, project)
    assert materials.retrieve(project, "inventory", root)["sources"] == []


def test_failed_import_keeps_previous_index(indexed):
    materials, root, project = indexed
    original = (root / "orders.py").read_text()
    (root / "orders.py").unlink()
    with pytest.raises(FileNotFoundError):
        materials.ingest(root, project)
    (root / "orders.py").write_text(original)
    assert materials.retrieve(project, "list_orders", root)["sources"]
