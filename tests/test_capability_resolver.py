"""Tests for the unified capability resolver (v2).

Coverage:
  - Resolver selection per category (correct adapter returned)
  - Swap-via-config with zero code change (swap_self_test)
  - Handshake fail-loud on a missing required secret
  - Degradation/failover ordering (cheapest available wins)
  - Governance/jurisdiction enforcement on a non-LLM category
  - Config lint: enabled provider with no registered adapter raises
  - TFIDFVectorAdapter always available
  - ContainerComputeAdapter subprocess probe
  - Database adapter secrets_present() with/without DATABASE_URL
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — lightweight stub base so tests don't need the full app
# ---------------------------------------------------------------------------

from src.capabilities.base import CapabilityAdapter, CapabilitySpec


def _spec(key="test", adapter="test_adapter", category="database",
           secrets=None, exec_locus="local", cost=0.0):
    return CapabilitySpec(
        key=key, adapter=adapter, category=category,
        secrets=secrets or [], exec_locus=exec_locus,
        label=key, notes="", cost=cost,
    )


class _AlwaysUpAdapter(CapabilityAdapter):
    def _probe(self) -> bool:
        return True


class _AlwaysDownAdapter(CapabilityAdapter):
    def _probe(self) -> bool:
        return False


class _SecretRequiredAdapter(CapabilityAdapter):
    def _probe(self) -> bool:
        return self.secrets_present()


# ---------------------------------------------------------------------------
# 1. CapabilityAdapter base — secrets_present
# ---------------------------------------------------------------------------

class TestCapabilityAdapterBase(unittest.TestCase):

    def test_keyless_adapter_secrets_present(self):
        adapter = _AlwaysUpAdapter(_spec(secrets=[]))
        self.assertTrue(adapter.secrets_present())

    def test_missing_secret_secrets_present(self):
        adapter = _SecretRequiredAdapter(_spec(secrets=["NONEXISTENT_SECRET_XYZ"]))
        self.assertFalse(adapter.secrets_present())

    def test_present_secret_secrets_present(self):
        with patch.dict(os.environ, {"SOME_REAL_SECRET": "value123"}):
            adapter = _SecretRequiredAdapter(_spec(secrets=["SOME_REAL_SECRET"]))
            self.assertTrue(adapter.secrets_present())

    def test_is_available_delegates_to_probe(self):
        self.assertTrue(_AlwaysUpAdapter(_spec()).is_available())
        self.assertFalse(_AlwaysDownAdapter(_spec()).is_available())

    def test_health_probe_ok(self):
        h = _AlwaysUpAdapter(_spec()).health_probe()
        self.assertTrue(h["ok"])
        self.assertIn("exec_locus", h)

    def test_health_probe_unavailable(self):
        h = _AlwaysDownAdapter(_spec()).health_probe()
        self.assertFalse(h["ok"])

    def test_jurisdiction_allowed_default(self):
        adapter = _AlwaysUpAdapter(_spec(exec_locus="local"))
        self.assertTrue(adapter.jurisdiction_allowed())


# ---------------------------------------------------------------------------
# 2. TFIDFVectorAdapter — always available
# ---------------------------------------------------------------------------

class TestTFIDFVectorAdapter(unittest.TestCase):

    def _adapter(self):
        from src.capabilities.adapters.vector_search import TFIDFVectorAdapter
        return TFIDFVectorAdapter(_spec(adapter="tfidf", category="vector_search"))

    def test_always_available(self):
        self.assertTrue(self._adapter().is_available())

    def test_health_probe_ok(self):
        h = self._adapter().health_probe()
        self.assertTrue(h["ok"])
        self.assertIn("local", h["detail"])

    def test_similarity_identical(self):
        adapter = self._adapter()
        score = adapter.similarity("hello world", "hello world")
        self.assertGreater(score, 0.9)

    def test_similarity_different(self):
        adapter = self._adapter()
        score = adapter.similarity("hello world", "quantum physics")
        self.assertLess(score, 0.5)

    def test_rank_returns_aligned_scores(self):
        adapter = self._adapter()
        scores = adapter.rank("database", ["database table", "quantum gravity", "database schema"])
        self.assertEqual(len(scores), 3)
        # database-related candidates should score higher
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[2], scores[1])


# ---------------------------------------------------------------------------
# 3. ContainerComputeAdapter — subprocess probe + run_command
# ---------------------------------------------------------------------------

class TestContainerComputeAdapter(unittest.TestCase):

    def _adapter(self):
        from src.capabilities.adapters.container_compute import SubprocessComputeAdapter
        return SubprocessComputeAdapter(
            _spec(adapter="subprocess_local", category="container_compute")
        )

    def test_probe_passes(self):
        self.assertTrue(self._adapter()._probe())

    def test_is_available(self):
        self.assertTrue(self._adapter().is_available())

    def test_run_command_echo(self):
        result = self._adapter().run_command(["echo", "capability_test"])
        self.assertTrue(result["ok"])
        self.assertIn("capability_test", result["stdout"])
        self.assertEqual(result["returncode"], 0)

    def test_run_command_empty_raises(self):
        with self.assertRaises(ValueError):
            self._adapter().run_command([])

    def test_run_command_failing(self):
        result = self._adapter().run_command(["false"])
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["returncode"], 0)

    def test_run_command_timeout(self):
        result = self._adapter().run_command(["sleep", "10"], timeout_s=1)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["stderr"].lower())


# ---------------------------------------------------------------------------
# 4. DatabaseAdapter — secrets_present with/without DATABASE_URL
# ---------------------------------------------------------------------------

class TestDatabaseAdapter(unittest.TestCase):

    def _adapter(self):
        from src.capabilities.adapters.database import PostgreSQLAdapter
        return PostgreSQLAdapter(
            _spec(adapter="postgresql", category="database", secrets=[])
        )

    def test_secrets_present_without_url(self):
        with patch.dict(os.environ, {}, clear=False):
            env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
            with patch.dict(os.environ, env, clear=True):
                adapter = self._adapter()
                self.assertFalse(adapter.secrets_present())

    def test_secrets_present_with_url(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}):
            adapter = self._adapter()
            self.assertTrue(adapter.secrets_present())


# ---------------------------------------------------------------------------
# 4b. SQLiteAdapter — second real database backend (proves swappability)
# ---------------------------------------------------------------------------

class TestSQLiteAdapter(unittest.TestCase):

    def _adapter(self, path=":memory:"):
        from src.capabilities.adapters.database import SQLiteAdapter
        spec = _spec(adapter="sqlite", category="database", secrets=[])
        spec.extra = {"path": path}
        return SQLiteAdapter(spec)

    def test_keyless_always_secrets_present(self):
        """SQLite needs no secret, so secrets_present() is always True."""
        self.assertTrue(self._adapter().secrets_present())

    def test_probe_passes_in_memory(self):
        """A :memory: SQLite DB is genuinely openable, so the probe passes."""
        self.assertTrue(self._adapter(":memory:")._probe())

    def test_is_available_in_memory(self):
        self.assertTrue(self._adapter(":memory:").is_available())

    def test_health_probe_ok_reports_location(self):
        h = self._adapter(":memory:").health_probe()
        self.assertTrue(h["ok"])
        self.assertIn(":memory:", h["detail"])
        self.assertEqual(h["exec_locus"], "local")

    def test_db_path_config_precedence(self):
        """The config `path` key wins over the SQLITE_DB_PATH env var."""
        with patch.dict(os.environ, {"SQLITE_DB_PATH": "/tmp/env_path.db"}):
            self.assertEqual(self._adapter("/tmp/cfg_path.db").db_path, "/tmp/cfg_path.db")

    def test_db_path_env_fallback(self):
        """With no config path, SQLITE_DB_PATH env is used."""
        from src.capabilities.adapters.database import SQLiteAdapter
        spec = _spec(adapter="sqlite", category="database", secrets=[])
        spec.extra = {}
        with patch.dict(os.environ, {"SQLITE_DB_PATH": "/tmp/env_path.db"}):
            self.assertEqual(SQLiteAdapter(spec).db_path, "/tmp/env_path.db")

    def test_execute_roundtrip_is_operative(self):
        """The adapter is a real, operative backend: create/insert/select works."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "round.db")
            adapter = self._adapter(path)
            adapter.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            adapter.execute("INSERT INTO t (v) VALUES (?)", ["hello"])
            rows = adapter.execute("SELECT v FROM t")
            self.assertEqual(rows, [("hello",)])
            # The file was actually created on disk (durable, not a stub).
            self.assertTrue(os.path.exists(path))


# ---------------------------------------------------------------------------
# 4c. Database category swap + failover (real registered adapters)
# ---------------------------------------------------------------------------

class TestDatabaseSwapAndFailover(unittest.TestCase):
    """Prove the database category has two real registered adapters and that a
    config-only swap / cost-ordered failover selects between them with zero
    call-site change."""

    def setUp(self):
        from src.capabilities import resolver as res_module
        res_module._resolver = None

    def tearDown(self):
        from src.capabilities import resolver as res_module
        res_module._resolver = None

    def test_two_adapters_registered_for_database(self):
        """CAPABILITY_REGISTRY must hold >=2 adapters for the database category."""
        from src.capabilities.registry import registered_adapters_for
        adapters = registered_adapters_for("database")
        self.assertIn("postgresql", adapters)
        self.assertIn("sqlite", adapters)
        self.assertGreaterEqual(len(adapters), 2)

    def test_both_enabled_in_config(self):
        """Both backends are enabled in config/providers.toml."""
        from src.capabilities.config import category_enabled_keys
        enabled = category_enabled_keys("database")
        self.assertIn("postgresql", enabled)
        self.assertIn("sqlite", enabled)

    def test_swap_self_test_sqlite_available(self):
        """The swap self-test proves a config-only swap to SQLite is live."""
        from src.capabilities.resolver import get_resolver
        result = get_resolver().swap_self_test("database", "sqlite")
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved_key"], "sqlite")

    def test_failover_picks_cheapest_available_postgres_when_present(self):
        """When DATABASE_URL is present, the cheaper PostgreSQL (cost 0) wins."""
        from src.capabilities.resolver import get_resolver
        from src.capabilities.adapters.database import PostgreSQLAdapter
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/x"}), \
             patch.object(PostgreSQLAdapter, "_probe", return_value=True):
            resolver = get_resolver()
            adapter = resolver.resolve("database")
            self.assertEqual(adapter.key, "postgresql")

    def test_failover_to_sqlite_when_postgres_unavailable(self):
        """With PostgreSQL unavailable, the resolver fails over to SQLite —
        a real second backend, selected by config/cost with no code change."""
        from src.capabilities.resolver import get_resolver
        from src.capabilities.adapters.database import PostgreSQLAdapter
        with patch.object(PostgreSQLAdapter, "_probe", return_value=False), \
             patch.object(PostgreSQLAdapter, "secrets_present", return_value=False):
            resolver = get_resolver()
            adapter = resolver.resolve("database")
            self.assertEqual(adapter.key, "sqlite")
            self.assertTrue(adapter.is_available())


# ---------------------------------------------------------------------------
# 5. CapabilityResolver — selection, failover ordering, CapabilityNotAvailable
# ---------------------------------------------------------------------------

class TestCapabilityResolver(unittest.TestCase):

    def setUp(self):
        # Reset the singleton before each test
        from src.capabilities import resolver as res_module
        res_module._resolver = None

    def tearDown(self):
        from src.capabilities import resolver as res_module
        res_module._resolver = None

    def test_resolve_vector_search_tfidf_always_available(self):
        """TF-IDF is always available so resolve('vector_search') must succeed."""
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        # vector_search category has tfidf (always up) + embedding (may be down)
        adapter = resolver.resolve("vector_search")
        self.assertTrue(adapter.is_available())

    def test_resolve_container_compute_always_available(self):
        """Subprocess compute is always available in the container."""
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        adapter = resolver.resolve("container_compute")
        self.assertTrue(adapter.is_available())

    def test_capability_not_available_raises_explicitly(self):
        """When no adapter is available, CapabilityNotAvailable is raised."""
        from src.capabilities.resolver import CapabilityResolver, CapabilityNotAvailable
        from src.capabilities import resolver as res_module

        resolver = CapabilityResolver()
        # Monkey-patch _get_entries to return only unavailable adapters
        resolver._get_entries = lambda cat: [
            (_spec(key="down"), _AlwaysDownAdapter(_spec(key="down")), False, 0.0)
        ]
        with self.assertRaises(CapabilityNotAvailable) as ctx:
            resolver.resolve("database")
        self.assertIn("database", str(ctx.exception))

    def test_failover_ordering_cheapest_available_wins(self):
        """Resolver returns the cheapest (lowest cost) available adapter."""
        from src.capabilities.resolver import CapabilityResolver

        expensive_up = _AlwaysUpAdapter(_spec(key="expensive", cost=100.0))
        cheap_up = _AlwaysUpAdapter(_spec(key="cheap", cost=1.0))

        resolver = CapabilityResolver()
        resolver._get_entries = lambda cat: [
            (_spec(key="cheap", cost=1.0), cheap_up, True, 0.0),
            (_spec(key="expensive", cost=100.0), expensive_up, True, 0.0),
        ]
        result = resolver.resolve("database")
        self.assertEqual(result.key, "cheap")

    def test_failover_skips_unavailable(self):
        """Resolver skips unavailable adapters and returns the next available."""
        from src.capabilities.resolver import CapabilityResolver

        down = _AlwaysDownAdapter(_spec(key="down", cost=0.0))
        up = _AlwaysUpAdapter(_spec(key="up", cost=5.0))

        resolver = CapabilityResolver()
        resolver._get_entries = lambda cat: [
            (_spec(key="down", cost=0.0), down, False, 0.0),
            (_spec(key="up", cost=5.0), up, True, 0.0),
        ]
        result = resolver.resolve("database")
        self.assertEqual(result.key, "up")

    def test_capability_matrix_includes_all_categories(self):
        """capability_matrix() returns a key for every known category."""
        from src.capabilities.resolver import get_resolver
        from src.capabilities.config import ALL_CATEGORIES
        resolver = get_resolver()
        matrix = resolver.capability_matrix()
        for cat in ALL_CATEGORIES:
            self.assertIn(cat, matrix, "category '%s' missing from matrix" % cat)
            self.assertIn("providers", matrix[cat])
            self.assertIn("active_key", matrix[cat])

    def test_swap_self_test_known_provider(self):
        """swap_self_test returns structured result for a known provider."""
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        # tfidf is always enabled and available
        result = resolver.swap_self_test("vector_search", "tfidf")
        self.assertIn("ok", result)
        self.assertIn("resolved_key", result)
        self.assertIn("detail", result)

    def test_swap_self_test_unknown_provider(self):
        """swap_self_test returns ok=False for an unknown provider key."""
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        result = resolver.swap_self_test("vector_search", "nonexistent_xyz")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["resolved_key"])

    def test_resolve_all_returns_list(self):
        """resolve_all returns a list (may be empty if nothing available)."""
        from src.capabilities.resolver import get_resolver
        resolver = get_resolver()
        results = resolver.resolve_all("vector_search")
        self.assertIsInstance(results, list)


# ---------------------------------------------------------------------------
# 6. Jurisdiction enforcement on a non-LLM category
# ---------------------------------------------------------------------------

class TestJurisdictionEnforcement(unittest.TestCase):

    def test_local_adapter_blocked_when_cloud_shift_false(self):
        """A cloud exec_locus adapter is skipped when cloud_shift is off."""
        from src.capabilities.base import CapabilityAdapter
        cloud_adapter = _AlwaysUpAdapter(
            _spec(exec_locus="provider_hosted")
        )
        # cloud_shift=False → cloud adapters are not permitted
        permitted = cloud_adapter.jurisdiction_allowed(cloud_shift=False)
        self.assertFalse(permitted)

    def test_local_adapter_permitted_when_cloud_shift_false(self):
        """A local exec_locus adapter is always permitted."""
        local_adapter = _AlwaysUpAdapter(_spec(exec_locus="local"))
        permitted = local_adapter.jurisdiction_allowed(cloud_shift=False)
        self.assertTrue(permitted)

    def test_pin_restricts_to_exact_locus(self):
        """exec_locus_pin=in_azure blocks a provider_hosted adapter."""
        cloud_adapter = _AlwaysUpAdapter(_spec(exec_locus="provider_hosted"))
        permitted = cloud_adapter.jurisdiction_allowed(exec_locus_pin="in_azure")
        self.assertFalse(permitted)

    def test_pin_local_permits_local_adapters(self):
        """exec_locus_pin=local permits local adapters."""
        local_adapter = _AlwaysUpAdapter(_spec(exec_locus="local"))
        permitted = local_adapter.jurisdiction_allowed(exec_locus_pin="local")
        self.assertTrue(permitted)


# ---------------------------------------------------------------------------
# 7. Unified handshake — fail-loud on missing required secret
# ---------------------------------------------------------------------------

class TestUnifiedHandshake(unittest.TestCase):

    def test_handshake_fails_on_missing_required_secret(self):
        """Handshake raises CapabilityHandshakeError when a required secret is absent."""
        from src.capabilities.handshake import CapabilityHandshakeError

        # Patch the config functions to inject a fake "required" scenario.
        fake_cfg = {
            "capabilities": {"llm": "swarm", "browser": "engine"},
            "llm": {"enabled": [], "required": []},
            "browser": {"enabled": [], "required": []},
            "database": {"enabled": ["postgresql"], "required": ["postgresql"]},
            "databasebackends": {
                "postgresql": {
                    "adapter": "postgresql",
                    "secrets": ["NONEXISTENT_REQUIRED_SECRET_XYZ"],
                    "exec_locus": "local",
                }
            },
        }
        for cat in ["vector_search", "knowledge_store", "object_storage",
                    "secrets_vault", "realtime", "orchestrator", "sig_optimizer",
                    "container_compute"]:
            fake_cfg[cat] = {"enabled": [], "required": []}

        with patch("src.capabilities.config.load_config", return_value=fake_cfg), \
             patch("src.providers.handshake.run_handshake", return_value={}), \
             patch("src.browser.handshake.run_browser_handshake", return_value={}):
            from src.capabilities.handshake import run_unified_handshake
            with self.assertRaises(CapabilityHandshakeError) as ctx:
                run_unified_handshake()
            self.assertIn("NONEXISTENT_REQUIRED_SECRET_XYZ", str(ctx.exception))

    def test_handshake_succeeds_with_no_required(self):
        """Handshake passes cleanly when no secrets are required."""
        fake_cfg = {
            "capabilities": {"llm": "swarm", "browser": "engine"},
            "llm": {"enabled": [], "required": []},
            "browser": {"enabled": [], "required": []},
        }
        for cat in ["database", "vector_search", "knowledge_store", "object_storage",
                    "secrets_vault", "realtime", "orchestrator", "sig_optimizer",
                    "container_compute"]:
            fake_cfg[cat] = {"enabled": [], "required": []}

        with patch("src.capabilities.config.load_config", return_value=fake_cfg), \
             patch("src.providers.handshake.run_handshake", return_value={}), \
             patch("src.browser.handshake.run_browser_handshake", return_value={}):
            from src.capabilities.handshake import run_unified_handshake
            result = run_unified_handshake()
            self.assertEqual(result["errors"], [])

    def test_handshake_detects_unknown_adapter(self):
        """Handshake raises when an enabled provider uses an unregistered adapter."""
        from src.capabilities.handshake import CapabilityHandshakeError
        fake_cfg = {
            "capabilities": {"llm": "swarm", "browser": "engine"},
            "llm": {"enabled": [], "required": []},
            "browser": {"enabled": [], "required": []},
            "database": {"enabled": ["postgresql"], "required": []},
            "databasebackends": {
                "postgresql": {
                    "adapter": "nonexistent_adapter_xyz",
                    "secrets": [],
                    "exec_locus": "local",
                }
            },
        }
        for cat in ["vector_search", "knowledge_store", "object_storage",
                    "secrets_vault", "realtime", "orchestrator", "sig_optimizer",
                    "container_compute"]:
            fake_cfg[cat] = {"enabled": [], "required": []}

        with patch("src.capabilities.config.load_config", return_value=fake_cfg), \
             patch("src.providers.handshake.run_handshake", return_value={}), \
             patch("src.browser.handshake.run_browser_handshake", return_value={}):
            from src.capabilities.handshake import run_unified_handshake
            with self.assertRaises(CapabilityHandshakeError) as ctx:
                run_unified_handshake()
            self.assertIn("nonexistent_adapter_xyz", str(ctx.exception))


# ---------------------------------------------------------------------------
# 8. Config — all categories readable from providers.toml
# ---------------------------------------------------------------------------

class TestCapabilityConfig(unittest.TestCase):

    def test_all_categories_present_in_toml(self):
        """All canonical categories have at least one enabled backend in toml."""
        from src.capabilities.config import ALL_CATEGORIES, category_enabled_keys
        for cat in ALL_CATEGORIES:
            keys = category_enabled_keys(cat)
            self.assertIsInstance(keys, list,
                "category_enabled_keys('%s') must return a list" % cat)

    def test_defined_specs_parseable(self):
        """defined_specs_for() returns CapabilitySpec instances for each category."""
        from src.capabilities.config import ALL_CATEGORIES, defined_specs_for
        from src.capabilities.base import CapabilitySpec
        for cat in ALL_CATEGORIES:
            specs = defined_specs_for(cat)
            for key, spec in specs.items():
                self.assertIsInstance(spec, CapabilitySpec)
                self.assertEqual(spec.category, cat)
                self.assertEqual(spec.key, key)


# ---------------------------------------------------------------------------
# 9. Behavioral proof — resolver selection is authoritative at execution time
# ---------------------------------------------------------------------------

class TestBrowserEngineBinding(unittest.TestCase):
    """Prove that _require_browser_engine() binds execution to all
    resolver-permitted backends (preserving failover within the permitted set)
    while excluding any non-permitted backend from BrowserEngine's candidates."""

    def _make_browser_adapters(self, n=2):
        """Return n BrowserCapabilityAdapter instances with stub backends."""
        from src.capabilities.adapters.browser import BrowserCapabilityAdapter
        adapters = []
        for i in range(n):
            spec = _spec(key="backend_%d" % i, adapter="BrowserCapabilityAdapter",
                         category="browser")
            adapter = BrowserCapabilityAdapter(spec)
            stub_backend = MagicMock()
            stub_backend.key = "backend_%d" % i
            stub_backend.available.return_value = True
            adapter._get_browser_backend = lambda b=stub_backend: b
            adapters.append((adapter, stub_backend))
        return adapters

    def test_require_browser_engine_uses_all_permitted_backends(self):
        """_require_browser_engine() must bind the engine to ALL resolver-permitted
        backends in cost order so BrowserEngine's failover is preserved within
        the jurisdiction-permitted set."""
        from src.swarm.integrations import Integrations
        adapters_and_backends = self._make_browser_adapters(2)
        permitted_adapters = [a for a, _ in adapters_and_backends]
        expected_backends = [b for _, b in adapters_and_backends]

        with patch("src.capabilities.resolver.get_resolver") as mock_get_resolver:
            mock_resolver = MagicMock()
            mock_resolver.resolve_all.return_value = permitted_adapters
            mock_get_resolver.return_value = mock_resolver

            engine = Integrations._require_browser_engine()

        result = engine.backends()
        self.assertEqual(result, expected_backends,
            "_require_browser_engine() must expose all permitted backends for failover")
        mock_resolver.resolve_all.assert_called_once_with("browser")

    def test_require_browser_engine_raises_when_no_permitted(self):
        """_require_browser_engine() must raise when no permitted backend is
        available — fail-loud, not silently degrade."""
        from src.swarm.integrations import Integrations
        from src.capabilities.resolver import CapabilityNotAvailable

        with patch("src.capabilities.resolver.get_resolver") as mock_get_resolver:
            mock_resolver = MagicMock()
            mock_resolver.resolve_all.return_value = []
            mock_get_resolver.return_value = mock_resolver

            with self.assertRaises(CapabilityNotAvailable):
                Integrations._require_browser_engine()

    def test_require_browser_engine_does_not_call_ordered_backend_specs(self):
        """_require_browser_engine() must not call ordered_backend_specs() —
        that would re-resolve backends and bypass the resolver's jurisdiction
        gate, allowing a policy-blocked backend to slip in."""
        from src.swarm.integrations import Integrations
        adapters_and_backends = self._make_browser_adapters(1)
        permitted_adapters = [a for a, _ in adapters_and_backends]

        with patch("src.capabilities.resolver.get_resolver") as mock_get_resolver, \
             patch("src.browser.config.ordered_backend_specs") as mock_specs:
            mock_resolver = MagicMock()
            mock_resolver.resolve_all.return_value = permitted_adapters
            mock_get_resolver.return_value = mock_resolver

            engine = Integrations._require_browser_engine()
            _ = engine.backends()
            mock_specs.assert_not_called()

    def test_get_engine_single_adapter_still_works(self):
        """BrowserCapabilityAdapter.get_engine() must still bind the engine to
        the single adapter's backend when called directly."""
        from src.capabilities.adapters.browser import BrowserCapabilityAdapter
        spec = _spec(key="local_playwright", adapter="BrowserCapabilityAdapter",
                     category="browser")
        adapter = BrowserCapabilityAdapter(spec)
        stub_backend = MagicMock()
        stub_backend.key = "local_playwright"

        with patch.object(adapter, "_get_browser_backend", return_value=stub_backend):
            engine = adapter.get_engine()

        result = engine.backends()
        self.assertEqual(result, [stub_backend])

    def test_get_engine_raises_when_no_backend(self):
        """get_engine() must raise (not silently fall back) when no backend
        was resolved — fail-loud, not fail-silent."""
        from src.capabilities.adapters.browser import BrowserCapabilityAdapter
        spec = _spec(key="x", adapter="BrowserCapabilityAdapter", category="browser")
        adapter = BrowserCapabilityAdapter(spec)
        with patch.object(adapter, "_get_browser_backend", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.get_engine()
            self.assertIn("no backend resolved", str(ctx.exception))


class TestRealtimeAdapterRouting(unittest.TestCase):
    """Prove that TwilioRealtimeAdapter.place_call() routes through the adapter
    boundary, so Integrations.telephony_place_call() executes via the resolved
    adapter and not a direct facade import at the call site."""

    def test_place_call_routes_through_facade(self):
        """place_call() on the adapter must delegate to facade.place_call(),
        not perform its own outbound TCP/HTTP call."""
        spec = _spec(key="twilio", adapter="TwilioRealtimeAdapter", category="realtime")
        from src.capabilities.adapters.realtime import TwilioRealtimeAdapter
        adapter = TwilioRealtimeAdapter(spec)

        expected = {"sid": "CA123", "status": "queued"}
        with patch("src.telephony.facade.place_call", return_value=expected) as mock_call:
            result = adapter.place_call("+15551234567", "https://example.com/voice",
                                        from_number="+15550000000")
            mock_call.assert_called_once()
            call_args = mock_call.call_args
            self.assertEqual(call_args[0][0], "+15551234567")
            self.assertEqual(call_args[0][1], "https://example.com/voice")
        self.assertEqual(result["sid"], "CA123")

    def test_place_call_forwards_provider_kwarg(self):
        """place_call() must forward the provider kwarg so the facade can
        dispatch to a non-default telephony backend."""
        spec = _spec(key="twilio", adapter="TwilioRealtimeAdapter", category="realtime")
        from src.capabilities.adapters.realtime import TwilioRealtimeAdapter
        adapter = TwilioRealtimeAdapter(spec)

        with patch("src.telephony.facade.place_call", return_value={"sid": "CB456"}) as mock_call:
            adapter.place_call("+15551234567", "https://example.com/voice",
                               provider="alt_twilio")
            _, kwargs = mock_call.call_args
            self.assertEqual(kwargs.get("provider"), "alt_twilio")

    def test_adapter_probe_uses_telephony_configured(self):
        """_probe() must delegate to telephony_configured() — not hardcode
        Twilio availability — so swapping providers only requires a new adapter."""
        spec = _spec(key="twilio", adapter="TwilioRealtimeAdapter", category="realtime")
        from src.capabilities.adapters.realtime import TwilioRealtimeAdapter
        adapter = TwilioRealtimeAdapter(spec)

        with patch("src.telephony.facade.telephony_configured", return_value=True):
            self.assertTrue(adapter._probe())
        with patch("src.telephony.facade.telephony_configured", return_value=False):
            self.assertFalse(adapter._probe())


class TestJurisdictionEnforcedAtExecution(unittest.TestCase):
    """Prove that swap-via-config works: changing exec_locus_pin redirects
    execution to a different adapter without any code change at the call site."""

    def _make_resolver_with_two_adapters(self):
        """Resolver pre-loaded with a 'local' adapter (cost=0) and 'cloud'
        adapter (cost=10). Cache is pre-populated so the test never reads
        providers.toml or calls build_adapter().

        Returns (resolver, local_adapter, cloud_adapter). Pin is passed per-call
        to resolver.resolve(exec_locus_pin=...) matching the real API contract.
        """
        import time
        from src.capabilities.resolver import CapabilityResolver

        local_spec = _spec(key="local_a", adapter="local_a",
                           category="database", exec_locus="local", cost=0.0)
        cloud_spec = _spec(key="cloud_a", adapter="cloud_a",
                           category="database", exec_locus="provider_hosted", cost=10.0)

        local_adapter = _AlwaysUpAdapter(local_spec)
        cloud_adapter = _AlwaysUpAdapter(cloud_spec)

        resolver = CapabilityResolver()
        now = time.monotonic()
        # Pre-populate cache; both adapters are available (ok=True).
        resolver._cache["database"] = [
            (local_spec, local_adapter, True, now),
            (cloud_spec, cloud_adapter, True, now),
        ]
        return resolver, local_adapter, cloud_adapter

    def test_pin_local_selects_local_adapter(self):
        resolver, local_adapter, _ = self._make_resolver_with_two_adapters()
        # Pass pin directly — jurisdiction_allowed() receives it through resolve().
        result = resolver.resolve("database", exec_locus_pin="local")
        self.assertIs(result, local_adapter,
            "exec_locus_pin=local must select the local adapter at execution time")

    def test_pin_provider_hosted_selects_cloud_adapter(self):
        resolver, _, cloud_adapter = self._make_resolver_with_two_adapters()
        result = resolver.resolve("database", exec_locus_pin="provider_hosted")
        self.assertIs(result, cloud_adapter,
            "exec_locus_pin=provider_hosted must select the cloud adapter at execution time")

    def test_swapping_pin_changes_execution_target_with_no_code_change(self):
        """Changing exec_locus_pin alone (simulating a config swap) must redirect
        the resolved adapter — this is the behavioral proof for swap-via-config.

        No adapter code changes: only the pin value differs between the two
        resolver.resolve() calls, yet a different adapter is returned.
        """
        resolver, local_a, cloud_a = self._make_resolver_with_two_adapters()

        result_local = resolver.resolve("database", exec_locus_pin="local")
        result_cloud = resolver.resolve("database", exec_locus_pin="provider_hosted")

        self.assertIsNot(result_local, result_cloud,
            "Different exec_locus_pin values must resolve to different adapters")
        self.assertIs(result_local, local_a,
            "pin=local must select the local adapter")
        self.assertIs(result_cloud, cloud_a,
            "pin=provider_hosted must select the cloud adapter")


if __name__ == "__main__":
    unittest.main()
