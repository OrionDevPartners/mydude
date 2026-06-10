"""Hume AI Expression Measurement client (httpx) — read-only language emotion.

Submits a batch job for the ``language`` model, polls it to completion, fetches
the predictions, and returns a NORMALIZED emotion result. Fails loud on auth
errors, job failure, timeout, or an unrecognized response schema — it never
fabricates an emotion (governance pillar #1).

Endpoint shape (verified June 2026):
  POST {base}/v0/batch/jobs           body {"models": {"language": {...}}, "text": [...]}
  GET  {base}/v0/batch/jobs/{id}      -> state.status COMPLETED | FAILED | ...
  GET  {base}/v0/batch/jobs/{id}/predictions
Auth: header ``X-Hume-Api-Key``.
"""
import logging
import time

import httpx

from src.coach.providers import (
    hume_credentials,
    CoachAuthError,
    CoachProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_POLL_INTERVAL = 2.0
_POLL_MAX = 90.0
_MAX_TEXT = 8000


class HumeClient:
    provider = "hume"
    model = "language"

    def __init__(self):
        creds = hume_credentials()
        self._key = creds["api_key"]
        self._base = creds["base_url"].rstrip("/")
        self.source = creds["source"]

    def _headers(self, json_ct=False):
        h = {"X-Hume-Api-Key": self._key, "Accept": "application/json"}
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def _raise_for_status(self, resp, what):
        if resp.status_code in (401, 403):
            raise CoachAuthError(
                "Hume rejected the request on %s (HTTP %d). Check HUME_API_KEY "
                "in the vault." % (what, resp.status_code)
            )
        if resp.status_code >= 400:
            raise CoachProviderError(
                "Hume API error on %s (HTTP %d): %s"
                % (what, resp.status_code, resp.text[:300])
            )

    def _submit(self, text):
        body = {
            "models": {"language": {"granularity": "sentence", "sentiment": {}}},
            "text": [text],
        }
        url = "%s/v0/batch/jobs" % self._base
        try:
            resp = httpx.post(url, json=body, headers=self._headers(True),
                              timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise CoachProviderError("Hume submit request failed: %s" % e)
        self._raise_for_status(resp, "submit job")
        data = resp.json()
        job_id = data.get("job_id") or data.get("id")
        if not job_id:
            raise CoachProviderError(
                "Hume submit returned no job id: %s" % str(data)[:200]
            )
        return job_id

    def _poll(self, job_id):
        url = "%s/v0/batch/jobs/%s" % (self._base, job_id)
        waited = 0.0
        while waited <= _POLL_MAX:
            try:
                resp = httpx.get(url, headers=self._headers(), timeout=_TIMEOUT)
            except httpx.HTTPError as e:
                raise CoachProviderError("Hume poll request failed: %s" % e)
            self._raise_for_status(resp, "poll job")
            data = resp.json()
            state = data.get("state") or {}
            status = (state.get("status") or "").upper()
            if status in ("COMPLETED", "DONE", "SUCCESS"):
                return
            if status in ("FAILED", "ERRORED", "ERROR"):
                raise CoachProviderError(
                    "Hume job failed: %s" % str(state)[:200]
                )
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
        raise CoachProviderError(
            "Hume job did not complete within %ds." % int(_POLL_MAX)
        )

    def _predictions(self, job_id):
        url = "%s/v0/batch/jobs/%s/predictions" % (self._base, job_id)
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise CoachProviderError("Hume predictions fetch failed: %s" % e)
        self._raise_for_status(resp, "fetch predictions")
        return resp.json()

    def analyze_text(self, text):
        """Submit -> poll -> fetch -> normalize. Fail loud on any failure."""
        if not text or not text.strip():
            raise CoachProviderError("Cannot analyze empty text.")
        clean = text.strip()[:_MAX_TEXT]
        job_id = self._submit(clean)
        self._poll(job_id)
        preds = self._predictions(job_id)
        return self._normalize(preds)

    def _normalize(self, preds):
        """Aggregate per-token emotion scores into a normalized result.

        Hume language predictions nest as:
          [ {results: {predictions: [ {models: {language:
              {grouped_predictions: [ {predictions: [
                  {emotions: [{name, score}...], sentiment: [{name, score}...]}
              ]} ]}}} ]}} ]
        """
        emotions_acc = {}
        n = 0
        sentiment_pairs = []  # (bucket 1..9, score)
        try:
            for item in (preds or []):
                results = item.get("results") or {}
                for pred in (results.get("predictions") or []):
                    lang = (pred.get("models") or {}).get("language") or {}
                    for grp in (lang.get("grouped_predictions") or []):
                        for p in (grp.get("predictions") or []):
                            for emo in (p.get("emotions") or []):
                                name, score = emo.get("name"), emo.get("score")
                                if name is None or score is None:
                                    continue
                                emotions_acc[name] = emotions_acc.get(name, 0.0) + float(score)
                            n += 1
                            for s in (p.get("sentiment") or []):
                                try:
                                    sentiment_pairs.append((float(s.get("name")),
                                                            float(s.get("score"))))
                                except (TypeError, ValueError):
                                    pass
        except (AttributeError, TypeError) as e:
            raise CoachProviderError("Unrecognized Hume response schema: %s" % e)

        if not emotions_acc or n == 0:
            raise CoachProviderError(
                "Hume returned no emotion predictions for the text."
            )

        averaged = sorted(
            ((k, v / n) for k, v in emotions_acc.items()),
            key=lambda kv: kv[1], reverse=True,
        )
        top = [{"name": k, "score": round(v, 4)} for k, v in averaged[:8]]

        valence = None
        sentiment_mean = None
        wsum = sum(sc for _, sc in sentiment_pairs)
        if wsum > 0:
            sentiment_mean = round(
                sum(b * sc for b, sc in sentiment_pairs) / wsum, 4
            )  # 1..9
            valence = round((sentiment_mean - 5.0) / 4.0, 4)  # map to -1..1

        return {
            "provider": self.provider,
            "model": self.model,
            "emotions": top,
            "label": top[0]["name"],
            "score": top[0]["score"],
            "valence": valence,
            "arousal": None,  # Hume language model does not emit arousal
            "sentiment_mean": sentiment_mean,
            "tokens_analyzed": n,
        }
