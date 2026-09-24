"""End-to-end tests for the gauge-block API and exact solver.

Runs against a live uvicorn server spawned as a subprocess, so the HTTP
contract (status codes, field-level errors, static page) is exercised for real.
Run with:  python -m unittest discover -s backend/tests
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(BACKEND_DIR, "app", "bin", "solver")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self):
        self.port = _free_port()
        self.proc = None

    def __enter__(self):
        env = dict(os.environ, SOLVER_BIN=BIN)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=BACKEND_DIR, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            self.proc.kill()
            raise RuntimeError("server did not start")
        return self

    def __exit__(self, *exc):
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def call(self, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/solve",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=10
        ) as resp:
            return resp.status, resp.read()


def block(ids_w):
    return [{"id": i, "length": w} for i, w in ids_w]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.srv.__exit__(None, None, None)

    def test_health_and_index(self):
        status, body = self.srv.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        status, html = self.srv.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<div id=\"root\">", html)

    def test_preemption_counterexample(self):
        # P=100,Q=115,X=40,Y=39 plus irrelevant huge blocks.
        # Gap#1=90(+-11): {P dev10} vs {X+Y dev11}; gap#2=100(+-15):
        # {P dev0} vs {Q dev15}. Naive order-grabbing of P gives max dev 15;
        # the globally optimal batch (X+Y -> #1, P -> #2) gives max dev 11.
        payload = {
            "blocks": block([
                ("P", 100), ("Q", 115), ("X", 40), ("Y", 39),
                ("Z1", 300), ("Z2", 301), ("Z3", 302), ("Z4", 303),
            ]),
            "targets": [
                {"target": 90, "tolerance": 11},
                {"target": 100, "tolerance": 15},
            ],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["maxAbsDev"], 11)
        self.assertEqual(data["canonical"][0], ["X", "Y"])
        self.assertEqual(data["canonical"][1], ["P"])
        self.assertEqual(data["gaps"][0]["deviation"], -11)
        self.assertEqual(data["gaps"][1]["deviation"], 0)
        self.assertEqual(data["totalOptimal"], "1")
        dest = {d["id"]: d for d in data["destinations"]}
        self.assertEqual(dest["P"]["kind"], "fixed")
        self.assertEqual(dest["P"]["gap"], 1)
        self.assertEqual(dest["Q"]["kind"], "always_unused")

    def test_infeasible_no_disjoint_assignment(self):
        # Only one block (B) sits near 50; two gaps of 50+-2 cannot both use
        # it, even though each gap individually is satisfiable.
        payload = {
            "blocks": block([("B", 50)] + [(f"T{i}", 31) for i in range(7)]),
            "targets": [
                {"target": 50, "tolerance": 2},
                {"target": 50, "tolerance": 2},
            ],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 422)
        self.assertFalse(data["ok"])
        self.assertTrue(data["infeasible"])

    def test_zero_tolerance_boundary(self):
        payload = {
            "blocks": block([("B", 50)] + [(f"T{i}", 31) for i in range(7)]),
            "targets": [
                {"target": 50, "tolerance": 0},
                {"target": 62, "tolerance": 0},
            ],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["maxAbsDev"], 0)

    def test_field_errors_keep_payload_echo(self):
        payload = {
            "blocks": [
                {"id": "A", "length": 10},
                {"id": "A", "length": 2.5},          # dup id + non-integer
                {"id": "C", "length": -3},           # range
            ] + [{"id": f"B{i}", "length": 1} for i in range(5)],
            "targets": [{"target": 5, "tolerance": -1}],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 422)
        fields = {e["field"] for e in data["errors"]}
        # 8 blocks present so the count is fine; check specific field reasons.
        self.assertIn("blocks[1].id", fields)
        self.assertIn("blocks[1].length", fields)
        self.assertIn("blocks[2].length", fields)
        self.assertIn("targets[0].tolerance", fields)
        self.assertEqual(data["echo"], payload)
        self.assertNotIn("totalOptimal", data)

    def test_cardinality_cap(self):
        # 8 unit blocks cannot serve four gaps of 3 (need 12 blocks).
        payload = {
            "blocks": block([(f"u{i}", 1) for i in range(8)]),
            "targets": [{"target": 3, "tolerance": 0} for _ in range(4)],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 422)
        self.assertTrue(data["infeasible"])

    def test_exact_count_matches_multinomial(self):
        # 16 equal 100nm blocks, targets 200/300/400/300 at zero tolerance:
        # every optimal solution is a partition into 2/3/4/3 blocks, and the
        # unused 4 blocks are forced.  Count = 16!/(2!3!4!3!4!) = 504504000.
        payload = {
            "blocks": block([(f"M{i:02d}", 100) for i in range(16)]),
            "targets": [
                {"target": 200, "tolerance": 0},
                {"target": 300, "tolerance": 0},
                {"target": 400, "tolerance": 0},
                {"target": 300, "tolerance": 0},
            ],
        }
        status, data = self.srv.call(payload)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["totalOptimal"], "504504000")
        self.assertEqual(data["usedCount"], 12)
        self.assertEqual(len(data["unused"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
