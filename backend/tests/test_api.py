"""API-level tests: validation, exact payloads, error echo."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post(payload):
    return client.post("/api/solve", json=payload)


def base_payload():
    return {
        "blocks": [
            {"label": f"B{i}", "length": v}
            for i, v in enumerate([4, 6, 11, 20, 30, 40, 50, 60])
        ],
        "targets": [
            {"length": 10, "tolerance": 1},
            {"length": 10, "tolerance": 0},
        ],
    }


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_preemption_counterexample_joint_optimum():
    # Gap 1 (10 +/-1): nearest is {4,6} with dev 0, but that pair is the
    # ONLY feasible subset for gap 2 (10 +/-0). Joint optimum must give the
    # pair to gap 2 and block 11 (dev 1) to gap 1: max dev 1, feasible.
    r = _post(base_payload())
    assert r.status_code == 200, r.text
    data = r.json()["result"]
    assert data["objective"]["max_abs_dev"] == 1
    assert data["objective"]["sum_abs_dev"] == 1
    assert data["objective"]["used_blocks"] == 3
    assert data["cooptimal_count"] == "1"
    by_label = {f["label"]: f for f in data["block_fates"]}
    assert by_label["B0"]["kind"] == "fixed" and by_label["B0"]["gap"] == 1
    assert by_label["B1"]["kind"] == "fixed" and by_label["B1"]["gap"] == 1
    assert by_label["B2"]["kind"] == "fixed" and by_label["B2"]["gap"] == 0
    for lbl in ("B3", "B4", "B5", "B6", "B7"):
        assert by_label[lbl]["kind"] == "unused"
    gap2 = data["gaps"][1]
    assert gap2["actual_length"] == 10 and gap2["deviation"] == 0
    assert gap2["blocks"] == ["B0", "B1"]


def test_duplicate_labels_field_error():
    p = base_payload()
    p["blocks"][3]["label"] = "B0"
    r = _post(p)
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    fields = {e["field"]: e["message"] for e in body["errors"]}
    assert "blocks[3].label" in fields
    # input echoed so the form can retain it
    assert body["input"] == p


def test_non_integer_and_range_errors():
    p = base_payload()
    p["blocks"][0]["length"] = 3.5  # float rejected
    p["blocks"][1]["length"] = -2
    p["targets"][0]["tolerance"] = -1
    r = _post(p)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"blocks[0].length", "blocks[1].length",
            "targets[0].tolerance"} <= fields


def test_wrong_cardinality():
    p = base_payload()
    p["blocks"] = p["blocks"][:7]  # 7 < 8
    r = _post(p)
    assert r.status_code == 422
    assert any(e["field"] == "blocks" for e in r.json()["errors"])

    p2 = base_payload()
    p2["targets"] = p2["targets"][:1]  # 1 < 2
    r2 = _post(p2)
    assert r2.status_code == 422
    assert any(e["field"] == "targets" for e in r2.json()["errors"])


def test_infeasible_gap_specific_reason():
    # eight unit blocks; target 8 needs 8 blocks but cap is 6.
    p = {
        "blocks": [{"label": f"U{i}", "length": 1} for i in range(8)],
        "targets": [{"length": 8, "tolerance": 0},
                    {"length": 1, "tolerance": 0}],
    }
    r = _post(p)
    assert r.status_code == 422
    body = r.json()
    assert any(e["field"] == "targets[0]" for e in body["errors"])
    assert body["input"] == p


def test_jointly_infeasible_despite_local_feasibility():
    # Both gaps can only be formed by the same six unit blocks.
    p = {
        "blocks": [{"label": f"U{i}", "length": 1} for i in range(6)]
        + [{"label": "X0", "length": 100}, {"label": "X1", "length": 100}],
        "targets": [{"length": 6, "tolerance": 0},
                    {"length": 6, "tolerance": 0}],
    }
    r = _post(p)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "allocation" in fields


def test_bad_json():
    r = client.post("/api/solve", content="{not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["ok"] is False


def test_numeric_strings_accepted_but_fractional_rejected():
    # The web form submits text values: strict integer strings are accepted,
    # fractional/exponent/blank strings must be rejected field-by-field.
    p = base_payload()
    p["blocks"][0] = {"label": "B0", "length": "4"}  # integer string OK
    p["targets"][1] = {"length": "10", "tolerance": "0"}
    r = _post(p)
    assert r.status_code == 200, r.text

    p["blocks"][1] = {"label": "B1", "length": "6.0"}  # not a strict int
    p["blocks"][2] = {"label": "B2", "length": "abc"}
    p["targets"][0] = {"length": "1e1", "tolerance": "1"}
    r2 = _post(p)
    assert r2.status_code == 422
    fields = {e["field"] for e in r2.json()["errors"]}
    assert {"blocks[1].length", "blocks[2].length",
            "targets[0].length"} <= fields


def test_count_is_string_for_bignum_precision():
    # Many interchangeable singletons -> co-optimal count can be large;
    # API must serialise as an exact decimal string.
    blocks = [{"label": f"S{i}", "length": 5} for i in range(8)]
    blocks += [{"label": f"T{i}", "length": 7} for i in range(8)]
    p = {
        "blocks": blocks,  # 16 blocks
        "targets": [
            {"length": 5, "tolerance": 0},
            {"length": 5, "tolerance": 0},
            {"length": 7, "tolerance": 0},
        ],
    }
    r = _post(p)
    assert r.status_code == 200, r.text
    c = r.json()["result"]["cooptimal_count"]
    assert isinstance(c, str) and c.isdigit()
    # choose ordered disjoint singles: 8*7 for the two 5-gaps * 8 for 7
    assert int(c) == 8 * 7 * 8
