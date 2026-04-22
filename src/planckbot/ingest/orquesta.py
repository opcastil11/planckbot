"""Orquesta triple source: pull tool-call triples from a hosted Orquesta
(`agent_logs` + `prompts` tables exposed via PostgREST on self-hosted
Supabase).

Orquesta stores each tool invocation as two rows in `agent_logs`:
  - category='tool_call'   — has details.tool_call.{name, parameters, tool_use_id}
  - category='tool_result' — has details.tool_result.{output, tool_use_id, ...}

This source pages through tool_call rows, joins each to the matching
tool_result by `tool_use_id`, and emits one triple per pair. Rows missing
a partner are skipped.

Auth: requires the service-role key; reads via PostgREST REST API.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from planckbot.ingest.base import TripleRecord, TripleSource


PAGE_SIZE = 1000


class OrquestaSource(TripleSource):
    name = "orquesta"

    def __init__(
        self,
        base_url: str,
        service_key: str,
        project_id: Optional[str] = None,
        http_get=None,
    ):
        """Args:
            base_url:    Supabase REST base, e.g. "https://db.orquesta.live".
            service_key: service-role key (PGRST-compatible JWT).
            project_id:  optional — filter to prompts belonging to this project.
            http_get:    injectable HTTP fetcher for tests. Takes (url, headers)
                         and returns the parsed JSON body. Defaults to urllib.
        """
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.project_id = project_id
        self._http_get = http_get or self._default_http_get

    # -- HTTP ---------------------------------------------------------------

    @staticmethod
    def _default_http_get(url: str, headers: dict) -> list:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str, params: dict) -> list:
        qs = urllib.parse.urlencode(params, safe=",.()*=")
        url = f"{self.base_url}/rest/v1/{path}?{qs}"
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Accept": "application/json",
        }
        return self._http_get(url, headers)

    # -- Fetch logic --------------------------------------------------------

    def _fetch_prompt_ids_for_project(self, limit: int) -> set[str]:
        """When a project filter is set, resolve the prompt_ids up front."""
        rows = self._get(
            "prompts",
            {
                "select": "id",
                "project_id": f"eq.{self.project_id}",
                "limit": str(limit),
                "order": "created_at.desc",
            },
        )
        return {r["id"] for r in rows}

    def _fetch_logs(
        self, category: str, limit: int, since: Optional[str]
    ) -> list[dict]:
        params = {
            "select": "id,prompt_id,sequence,timestamp,details",
            "category": f"eq.{category}",
            "order": "timestamp.asc",
            "limit": str(limit),
        }
        if since:
            params["timestamp"] = f"gte.{since}"
        return self._get("agent_logs", params)

    def fetch(
        self, limit: int = 1000, since: Optional[str] = None
    ) -> Iterator[TripleRecord]:
        prompt_filter: Optional[set[str]] = None
        if self.project_id:
            prompt_filter = self._fetch_prompt_ids_for_project(limit * 4)
            if not prompt_filter:
                return

        # Pull more raw rows than `limit` because we pair them down.
        raw_limit = min(limit * 2, PAGE_SIZE)
        calls = self._fetch_logs("tool_call", raw_limit, since)
        results = self._fetch_logs("tool_result", raw_limit, since)

        # Index results by tool_use_id for O(1) pairing.
        results_by_use_id: dict[str, dict] = {}
        for r in results:
            details = r.get("details") or {}
            tr = details.get("tool_result") or {}
            use_id = tr.get("tool_use_id")
            if use_id:
                results_by_use_id[use_id] = r

        emitted = 0
        for call in calls:
            if emitted >= limit:
                break
            if prompt_filter and call.get("prompt_id") not in prompt_filter:
                continue
            details = call.get("details") or {}
            tc = details.get("tool_call") or {}
            use_id = tc.get("tool_use_id")
            if not use_id or use_id not in results_by_use_id:
                continue
            result_row = results_by_use_id[use_id]
            tr = (result_row.get("details") or {}).get("tool_result") or {}

            output = tr.get("output")
            if output is None and tr.get("error"):
                # Tool failed — skip; we don't want to train on error paths.
                continue

            yield TripleRecord(
                tool_name=tc.get("name") or "unknown",
                input_data=tc.get("parameters") or {},
                output_data=output if output is not None else "",
                context_data=None,
                session_id=call.get("prompt_id"),
            )
            emitted += 1
