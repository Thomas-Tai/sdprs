# -*- coding: utf-8 -*-
"""Tests for webcam client DB schema and key-based auth (Task 1)."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from central_server.database import (
    init_db,
    close_db,
    create_webcam_client,
    get_webcam_client_by_key,
    revoke_webcam_key,
    register_webcam_cameras,
    get_webcam_cameras,
    get_webcam_camera_owner,
)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    init_db(db_path)
    yield
    close_db()


def test_create_webcam_client():
    result = create_webcam_client("櫃台電腦")
    assert result["node_id"].startswith("webcam_")
    assert result["api_key"].startswith("sk-webcam-")
    assert len(result["api_key"]) > 30
    expected_hash = hashlib.sha256(result["api_key"].encode()).hexdigest()
    assert result["api_key_hash"] == expected_hash


def test_get_webcam_client_by_key():
    created = create_webcam_client("Test PC")
    found = get_webcam_client_by_key(created["api_key"])
    assert found is not None
    assert found["node_id"] == created["node_id"]
    assert found["name"] == "Test PC"


def test_get_webcam_client_by_key_invalid():
    assert get_webcam_client_by_key("sk-webcam-nonexistent") is None


def test_revoke_webcam_key():
    created = create_webcam_client("Revoke Test")
    new_key = revoke_webcam_key(created["node_id"])
    assert new_key["api_key"] != created["api_key"]
    assert get_webcam_client_by_key(created["api_key"]) is None
    assert get_webcam_client_by_key(new_key["api_key"]) is not None


def test_register_webcam_cameras():
    client = create_webcam_client("Cam Test PC")
    cameras = [
        {"name": "Front Door", "device_index": 0, "resolution": [1280, 720], "jpeg_quality": 50, "target_fps": 10},
        {"name": "Back Yard"},
    ]
    result = register_webcam_cameras(client["node_id"], cameras)
    assert len(result) == 2
    assert result[0]["name"] == "Front Door"
    assert result[1]["name"] == "Back Yard"
    assert result[0]["node_id"].startswith("webcam_")
    assert result[1]["node_id"].startswith("webcam_")


def test_get_webcam_cameras():
    client = create_webcam_client("List Cams PC")
    register_webcam_cameras(client["node_id"], [
        {"name": "Cam A", "device_index": 0},
        {"name": "Cam B", "device_index": 1},
    ])
    cams = get_webcam_cameras(client["node_id"])
    assert len(cams) == 2
    names = {c["name"] for c in cams}
    assert names == {"Cam A", "Cam B"}
    for c in cams:
        assert c["status"] == "OFFLINE"


def test_get_webcam_camera_owner():
    client = create_webcam_client("Owner PC")
    cams = register_webcam_cameras(client["node_id"], [{"name": "Owned Cam"}])
    cam_node_id = cams[0]["node_id"]
    api_key_hash = hashlib.sha256(client["api_key"].encode()).hexdigest()

    owner = get_webcam_camera_owner(cam_node_id, api_key_hash)
    assert owner is not None
    assert owner["node_id"] == cam_node_id
    assert owner["client_id"] == client["node_id"]

    # Wrong key hash should return None
    wrong_hash = hashlib.sha256(b"wrong-key").hexdigest()
    assert get_webcam_camera_owner(cam_node_id, wrong_hash) is None
