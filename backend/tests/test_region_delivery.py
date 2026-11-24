"""区域授权交付测试：跨界格逐点子区裁切、授权更正、匿名发包、审校边界与重领范围。

场景来源（交付需求）：
- 一个网格跨授权区和未授权区；合作方已领用旧区域的一段汇总；
- 授权更正只收窄部分子区（丙），后续还要增领未取过的窗口；
- 新请求明确申请甲、乙两个子区的匿名统计：提出时只获准甲，发送前追加授权覆盖乙，
  甲区许可始终有效；两个子区的贡献均有原始位置依据；
- 新下载只能含当前授权内的点，已交付结果仍能内部复核；
- 审校员越权查看其他区域或请求整格明细时，只留边界说明、不出数据。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


JIA_POLY = [[39.89, 116.40], [39.89, 116.42], [39.90, 116.42], [39.90, 116.40]]
YI_POLY = [[39.90, 116.41], [39.90, 116.42], [39.91, 116.42], [39.91, 116.41]]
BING_POLY = [[39.90, 116.40], [39.90, 116.41], [39.91, 116.41], [39.91, 116.40]]

# 确定性批次点：G1=wx4fcp（甲4乙2，跨界）、G2=wx4fcn（甲3，完全在授权区内）、
# G3=wx4fbz（甲2丙3，跨界且丙将被收窄）。
B2_POINTS = [
    # G1 跨界格：甲侧 4 点、乙侧 2 点
    {"point_id": 1, "lat": 39.8999, "lon": 116.4150, "terminal": "TERM-S1", "timestamp": 1.0},
    {"point_id": 2, "lat": 39.8998, "lon": 116.4152, "terminal": "TERM-S2", "timestamp": 2.0},
    {"point_id": 3, "lat": 39.8997, "lon": 116.4148, "terminal": "TERM-S3", "timestamp": 3.0},
    {"point_id": 4, "lat": 39.8996, "lon": 116.4154, "terminal": "TERM-S4", "timestamp": 4.0},
    {"point_id": 5, "lat": 39.9001, "lon": 116.4150, "terminal": "TERM-S5", "timestamp": 5.0},
    {"point_id": 6, "lat": 39.9002, "lon": 116.4152, "terminal": "TERM-S6", "timestamp": 6.0},
    # G2 完全在甲子区内：3 点
    {"point_id": 7, "lat": 39.8950, "lon": 116.4150, "terminal": "TERM-S7", "timestamp": 7.0},
    {"point_id": 8, "lat": 39.8955, "lon": 116.4155, "terminal": "TERM-S8", "timestamp": 8.0},
    {"point_id": 9, "lat": 39.8960, "lon": 116.4145, "terminal": "TERM-S9", "timestamp": 9.0},
    # G3 跨界格：甲侧 2 点、丙侧 3 点
    {"point_id": 10, "lat": 39.8999, "lon": 116.4050, "terminal": "TERM-S10", "timestamp": 10.0},
    {"point_id": 11, "lat": 39.8998, "lon": 116.4052, "terminal": "TERM-S11", "timestamp": 11.0},
    {"point_id": 12, "lat": 39.9001, "lon": 116.4050, "terminal": "TERM-S12", "timestamp": 12.0},
    {"point_id": 13, "lat": 39.9002, "lon": 116.4052, "terminal": "TERM-S13", "timestamp": 13.0},
    {"point_id": 14, "lat": 39.9003, "lon": 116.4055, "terminal": "TERM-S14", "timestamp": 14.0},
]

OLD_POINTS = [
    {"point_id": 101, "lat": 39.8999, "lon": 116.4150, "terminal": "TERM-OLD1", "timestamp": 101.0},
    {"point_id": 102, "lat": 39.9002, "lon": 116.4052, "terminal": "TERM-OLD2", "timestamp": 102.0},
]

PARTNER = "PARTNER-NORTH"
OTHER_PARTNER = "PARTNER-SOUTH"
ALL_WINDOWS = ["W1", "W2", "W3"]


@pytest.fixture()
def delivery_service(monkeypatch):
    """每个用例独立数据目录，并注册甲、乙、丙三个子区。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="region_delivery_test_"))
    import app.services.base as base_mod
    monkeypatch.setattr(base_mod, "DATA_ROOT", tmpdir)
    for sub in ["geo_points", "coord_transformed", "grid_subzone_stats",
                "region_delivery/packages", "region_delivery/internal", "checkpoints"]:
        (tmpdir / sub).mkdir(parents=True, exist_ok=True)

    from app.services.region_delivery import RegionDeliveryService
    svc = RegionDeliveryService()
    svc.register_subzone("JIA", "甲子区", JIA_POLY, parent_region="北方合作区")
    svc.register_subzone("YI", "乙子区", YI_POLY, parent_region="北方合作区")
    svc.register_subzone("BING", "丙子区", BING_POLY, parent_region="北方合作区")
    yield svc
    shutil.rmtree(tmpdir, ignore_errors=True)


def _index(svc, points_by_geohash: dict[str, list[dict]]) -> dict[str, dict]:
    return {row["geohash"]: row for row in points_by_geohash}


class TestCrossBoundaryContribution:
    """逐点依据原始位置统计跨界格实际贡献的子区。"""

    def test_stats_split_each_grid_by_real_subzone(self, delivery_service):
        svc = delivery_service
        svc.write_batch_points("B2", B2_POINTS)
        result = svc.compute_grid_subzone_stats("B2")
        assert result["grids"] == 3
        assert set(result["cross_boundary_grids"]) == {"wx4fcp", "wx4fbz"}

        grids = _index(svc, svc.get_grid_subzone_stats("B2"))
        g1 = {c["subzone_code"]: c["point_count"] for c in grids["wx4fcp"]["subzone_contrib"]}
        assert g1 == {"JIA": 4, "YI": 2}
        assert grids["wx4fcp"]["total_points"] == 6

        g2 = {c["subzone_code"]: c["point_count"] for c in grids["wx4fcn"]["subzone_contrib"]}
        assert g2 == {"JIA": 3}
        assert grids["wx4fcn"]["cross_boundary"] is False

        g3 = {c["subzone_code"]: c["point_count"] for c in grids["wx4fbz"]["subzone_contrib"]}
        assert g3 == {"JIA": 2, "BING": 3}
        assert grids["wx4fbz"]["total_points"] == 5

    def test_verify_cross_boundary_contribution_reconciles_totals(self, delivery_service):
        svc = delivery_service
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        checks = svc.verify_cross_boundary_contribution("B2")
        assert len(checks) == 2
        for item in checks:
            assert item["contrib_check_passed"] is True
            assert item["cross_boundary"] is True
        by_gh = {item["geohash"]: item for item in checks}
        assert {c["subzone_code"] for c in by_gh["wx4fcp"]["subzone_contrib"]} == {"JIA", "YI"}
        assert {c["subzone_code"] for c in by_gh["wx4fbz"]["subzone_contrib"]} == {"JIA", "BING"}


class TestAuthorizationDeliveryFlow:
    """完整授权-更正-发包-复核流程。"""

    def test_full_flow_scope_timing_anonymous_rows_and_reclaim(self, delivery_service):
        svc = delivery_service

        # 1) 旧区域汇总：合作方最初持有甲、乙、丙，已领用旧批次 W1 窗口。
        svc.grant_authorization(PARTNER, ["JIA", "YI", "BING"], note="初始授权")
        svc.write_batch_points("B1-OLD", OLD_POINTS)
        svc.compute_grid_subzone_stats("B1-OLD")
        svc.create_delivery_request(
            "REQ-OLD", PARTNER, "B1-OLD", ["JIA", "YI", "BING"], windows=["W1"])
        old_pkg = svc.deliver_request("REQ-OLD")
        assert old_pkg["row_count"] >= 1
        old_internal = svc.internal_review_package("REQ-OLD")
        assert old_internal["full_grid_subzone_stats"]  # 内部完整副本留存

        # 2) 授权更正：只收窄丙子区，甲、乙许可仍有效。
        svc.revoke_authorization(PARTNER, ["BING"], note="边界授权更正，收窄丙子区")
        assert svc.effective_authorization(PARTNER) == ["JIA", "YI"]

        # 3) 新请求明确申请甲、乙；提出请求时只获准甲（先临时收窄乙制造时点差）。
        svc.revoke_authorization(PARTNER, ["YI"], note="请求提出时乙尚未覆盖")
        assert svc.effective_authorization(PARTNER) == ["JIA"]
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        request = svc.create_delivery_request(
            "REQ-NEW", PARTNER, "B2", ["JIA", "YI"], windows=["W2", "W3"])
        assert request["authorization_at_request"] == ["JIA"]

        # 4) 发送前追加授权覆盖乙，甲区许可仍有效。
        svc.grant_authorization(PARTNER, ["YI"], note="发送前追加乙子区授权")
        assert svc.effective_authorization(PARTNER) == ["JIA", "YI"]

        # 5) 发包：核对同一请求实际采用的范围、匿名统计值与仍可重领范围。
        pkg = svc.deliver_request("REQ-NEW")
        assert pkg["authorization_used_at_delivery"] == ["JIA", "YI"]
        assert pkg["delivered_subzones"] == ["JIA", "YI"]
        assert pkg["denied_subzones"] == []

        # 行数：G1 甲乙各 1 行 + G2 甲 1 行 + G3 甲 1 行 = 4 行；G3 丙贡献截留。
        assert pkg["row_count"] == 4
        rows = _index_rows(pkg["rows"])
        assert rows[("wx4fcp", "JIA")]["point_count"] == 4
        assert rows[("wx4fcp", "YI")]["point_count"] == 2
        assert rows[("wx4fcn", "JIA")]["point_count"] == 3
        assert rows[("wx4fbz", "JIA")]["point_count"] == 2
        assert ("wx4fbz", "BING") not in rows

        # 边界说明：G3 为跨界格且丙未授权，截留贡献留痕、不出数据。
        note_codes = [note["code"] for note in pkg["boundary_notes"]]
        assert "GRID_PARTIAL_WITHHELD" in note_codes
        withheld_note = next(n for n in pkg["boundary_notes"] if n["code"] == "GRID_PARTIAL_WITHHELD")
        assert withheld_note["target"] == "wx4fbz"
        assert withheld_note["withheld"] == [
            {"subzone_code": "BING", "subzone_name": "丙子区", "point_count": 3}]

        # 6) 匿名化自检：终端标识与可还原单条轨迹的字段不得随包发出。
        check = svc.assert_package_is_anonymous("REQ-NEW")
        assert check["anonymous"] is True
        assert check["leaked_fields"] == []
        flat = json_text(pkg)
        for forbidden in ["TERM-", "orig_lat", "trans_lat", "timestamp", "point_id"]:
            assert forbidden not in flat

        # 7) 仍可重领范围：甲乙的 W2/W3 已交付，未取过的窗口体现为待领；丙被阻断。
        claimable = svc.get_claimable_scope(PARTNER, ALL_WINDOWS)
        by_code = {item["subzone_code"]: item for item in claimable["subzones"]}
        assert set(by_code) == {"JIA", "YI"}
        assert by_code["JIA"]["delivered_windows"] == ["W1", "W2", "W3"]
        assert by_code["JIA"]["available_windows"] == []
        assert by_code["YI"]["available_windows"] == ["W1"]  # 乙未领过 W1

        reclaim = svc.get_reclaimable_scope(PARTNER, ALL_WINDOWS)
        rc = {item["subzone_code"]: item for item in reclaim["subzones"]}
        assert rc["JIA"]["status"] == "authorized"
        assert rc["JIA"]["reclaimable_windows"] == []
        assert rc["YI"]["reclaimable_windows"] == ["W1"]
        assert rc["BING"]["status"] == "revoked"
        assert rc["BING"]["reclaimable_windows"] == []
        assert "收窄" in rc["BING"]["blocked_note"]

        # 8) 已交付结果仍能在内部复核，且内部副本含未授权丙的完整贡献。
        internal = svc.internal_review_package("REQ-NEW")
        full = {g["geohash"]: g for g in internal["full_grid_subzone_stats"]}
        g3_full = {c["subzone_code"]: c["point_count"] for c in full["wx4fbz"]["subzone_contrib"]}
        assert g3_full == {"JIA": 2, "BING": 3}
        assert internal["package_manifest"]["delivered_subzones"] == ["JIA", "YI"]

        # 9) 交付任务登记并已流转。
        entries = [r for r in __import__("app.store", fromlist=["store"]).store.rows(svc.MODULE)]
        delivered = [r for r in entries if r.get("请求编号") == "REQ-NEW"]
        assert len(delivered) == 1
        assert delivered[0]["status"] == "已交付"
        assert delivered[0]["匿名行数"] == "4"

    def test_new_download_after_further_narrowing_excludes_revoked_subzone(self, delivery_service):
        """授权再收窄后，任何新下载只能含当前授权内的点。"""
        svc = delivery_service
        svc.grant_authorization(PARTNER, ["JIA", "YI"])
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        svc.create_delivery_request("REQ-A", PARTNER, "B2", ["JIA", "YI"])
        pkg_a = svc.deliver_request("REQ-A")
        assert {row["subzone_code"] for row in pkg_a["rows"]} == {"JIA", "YI"}

        # 发送另一份新请求前再次收窄乙。
        svc.revoke_authorization(PARTNER, ["YI"], note="二次收窄")
        svc.create_delivery_request("REQ-B", PARTNER, "B2", ["JIA", "YI"])
        pkg_b = svc.deliver_request("REQ-B")
        assert pkg_b["delivered_subzones"] == ["JIA"]
        assert pkg_b["denied_subzones"] == ["YI"]
        assert {row["subzone_code"] for row in pkg_b["rows"]} == {"JIA"}
        codes = [n["code"] for n in pkg_b["boundary_notes"]]
        assert "SUBZONE_NOT_AUTHORIZED" in codes
        assert "GRID_PARTIAL_WITHHELD" in codes  # G1 中乙贡献被截留
        # 已交付的 A 包不被追溯修改，仍可内部复核。
        assert svc.get_package("REQ-A")["delivered_subzones"] == ["JIA", "YI"]
        assert svc.internal_review_package("REQ-A")["full_grid_subzone_stats"]


class TestReviewerBoundary:
    """审校员只能查看与该包对应的共享观测归属，越权只留边界说明。"""

    def test_reviewer_sees_only_authorized_attribution(self, delivery_service):
        svc = delivery_service
        svc.grant_authorization(PARTNER, ["JIA", "YI"])
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        svc.create_delivery_request("REQ-NEW", PARTNER, "B2", ["JIA", "YI"])
        svc.deliver_request("REQ-NEW")

        # 本包审校员持甲、乙授权：可见全部 4 行。
        own_view = svc.review_package("REQ-NEW", PARTNER)
        assert own_view["row_count"] == 4
        assert {r["subzone_code"] for r in own_view["rows"]} == {"JIA", "YI"}

        # 其他区域合作方只持丙授权（丙不在该包），无任何可见数据，只留边界说明。
        svc.grant_authorization(OTHER_PARTNER, ["BING"])
        other_view = svc.review_package("REQ-NEW", OTHER_PARTNER)
        assert other_view["row_count"] == 0
        assert other_view["rows"] == []
        codes = {n["code"] for n in other_view["boundary_notes"]}
        assert "NO_AUTHORIZED_OBSERVATION" in codes

        # 只持甲授权的审校员请求整格明细：乙行不出，整格请求被边界说明拦截。
        svc.register_subzone("DING", "丁审校区", JIA_POLY, parent_region="审校区")
        svc.grant_authorization("REVIEWER-JIA", ["JIA"])
        jia_view = svc.review_package("REQ-NEW", "REVIEWER-JIA", full_grid=True)
        assert {r["subzone_code"] for r in jia_view["rows"]} == {"JIA"}
        assert jia_view["row_count"] == 3  # G1甲、G2甲、G3甲
        review_codes = {n["code"] for n in jia_view["boundary_notes"]}
        assert "REVIEW_SCOPE_WITHHELD" in review_codes
        assert "FULL_GRID_DETAIL_WITHHELD" in review_codes
        flat = json_text(jia_view)
        assert "BING" not in flat and "TERM-" not in flat

    def test_internal_review_keeps_full_attribution_after_revoke(self, delivery_service):
        svc = delivery_service
        svc.grant_authorization(PARTNER, ["JIA", "YI", "BING"])
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        svc.create_delivery_request("REQ-OLD", PARTNER, "B2", ["JIA", "BING"])
        svc.deliver_request("REQ-OLD")
        svc.revoke_authorization(PARTNER, ["BING"])

        internal = svc.internal_review_package("REQ-OLD")
        full = {g["geohash"]: g for g in internal["full_grid_subzone_stats"]}
        assert full["wx4fbz"]["cross_boundary"] is True
        bing = next(c for c in full["wx4fbz"]["subzone_contrib"] if c["subzone_code"] == "BING")
        assert bing["point_count"] == 3
        # 外发包依然只有授权时交付的两个子区。
        pkg = svc.get_package("REQ-OLD")
        assert set(pkg["delivered_subzones"]) == {"BING", "JIA"}


class TestEdgeCases:
    """授权空窗与未登记子区的边界处理。"""

    def test_request_without_any_authorization_emits_notes_only(self, delivery_service):
        svc = delivery_service
        svc.write_batch_points("B2", B2_POINTS)
        svc.compute_grid_subzone_stats("B2")
        svc.create_delivery_request("REQ-EMPTY", "PARTNER-NOAUTH", "B2", ["JIA", "YI"])
        pkg = svc.deliver_request("REQ-EMPTY")
        assert pkg["row_count"] == 0
        assert pkg["delivered_subzones"] == []
        assert pkg["grid_count"] == 0
        codes = {n["code"] for n in pkg["boundary_notes"]}
        assert "SUBZONE_NOT_AUTHORIZED" in codes
        assert "GRID_PARTIAL_WITHHELD" in codes
        assert svc.assert_package_is_anonymous("REQ-EMPTY")["anonymous"] is True

    def test_unknown_subzone_grant_rejected(self, delivery_service):
        svc = delivery_service
        with pytest.raises(ValueError):
            svc.grant_authorization(PARTNER, ["NO-SUCH-ZONE"])


def _index_rows(rows):
    return {(row["geohash"], row["subzone_code"]): row for row in rows}


def json_text(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
