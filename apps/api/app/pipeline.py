"""Orchestration: ingest -> fast OCR -> classify -> rules -> (accurate OCR) -> (Claude fallback)."""

import asyncio
import logging
import time
from dataclasses import dataclass

from . import extractors, ingest
from .classify import TYPES, classify
from .config import Settings
from .extractors import Ctx, Extraction
from .llm import LlmClient, LlmError
from .ocr import Line, OcrEngines, native_to_lines, ocr_page
from .textutils import entities, norm

log = logging.getLogger(__name__)

# fields whose local value is trusted over the LLM (checksum-validated)
PROTECTED_SOURCES = {"mrz", "mrz+ocr"}


@dataclass
class Options:
    document_type: str | None = None
    llm: str | None = None  # None = server default; "off" | "fallback" | "always"
    include_text: bool = False


class Pipeline:
    def __init__(self, settings: Settings):
        self.s = settings
        self.engines = OcrEngines(settings.ocr_threads)
        self.sem = asyncio.Semaphore(settings.ocr_concurrency)
        self.llm: LlmClient | None = None
        if settings.llm_mode != "off":
            try:
                self.llm = LlmClient(settings)
            except Exception as e:  # noqa: BLE001 - missing credentials must not prevent local extraction
                log.warning("LLM fallback disabled: %s", e)

    def warmup(self) -> None:
        sizes = [self.s.ocr_fast_model]
        if self.s.ocr_accurate_model != "none":
            sizes.append(self.s.ocr_accurate_model)
        self.engines.warmup(sizes)

    # ------------------------------------------------------------------ OCR

    def _ocr_pages(self, pages: list[ingest.Page], size: str) -> list[Line]:
        """OCR all pages; stack them vertically so layout helpers keep working across pages."""
        out: list[Line] = []
        y_off = 0.0
        for p in pages:
            if p.is_native:
                lines = native_to_lines(p.native_lines)
            else:
                lines = ocr_page(self.engines.get(size), p.image)
            for ln in lines:
                out.append(Line(ln.text, ln.conf, ln.x0, ln.y0 + y_off, ln.x1, ln.y1 + y_off))
            if lines:
                y_off = max(l.y1 for l in out) + 200
        return out

    async def _ocr(self, pages, size) -> list[Line]:
        async with self.sem:
            return await asyncio.to_thread(self._ocr_pages, pages, size)

    def _analyse(self, lines: list[Line], forced_type: str | None) -> tuple[Ctx, Extraction, dict]:
        ctx = Ctx(lines)
        if forced_type:
            doc_type, scores = forced_type, {}
        else:
            doc_type, scores = classify(ctx.ntext, ctx.row_texts)
        return ctx, extractors.run(doc_type, ctx), scores

    # ------------------------------------------------------------------ main

    async def process(self, data: bytes, opts: Options) -> dict:
        timings: dict[str, int] = {}
        t0 = time.perf_counter()

        def lap(name: str, since: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - since) * 1000)
            return now

        kind, pages = await asyncio.to_thread(ingest.load, data, self.s.max_pdf_pages, self.s.ocr_max_side)
        t = lap("ingest", t0)
        has_images = any(not p.is_native for p in pages)
        forced = opts.document_type if opts.document_type in TYPES and opts.document_type != "unknown" else None

        passes = []
        lines = await self._ocr(pages, self.s.ocr_fast_model)
        passes.append("native" if not has_images else self.s.ocr_fast_model)
        ctx, ex, scores = self._analyse(lines, forced)
        t = lap("ocr_fast", t)

        llm_mode = opts.llm or self.s.llm_mode
        llm_ready = self.llm is not None and llm_mode != "off"
        # Second, more accurate OCR pass when rules are not satisfied (skip if the LLM will look at the image anyway)
        if (not ex.complete and has_images and self.s.ocr_accurate_model != "none"
                and not (ex.llm_preferred and llm_ready)):
            lines2 = await self._ocr(pages, self.s.ocr_accurate_model)
            ctx2, ex2, scores2 = self._analyse(lines2, forced)
            passes.append(self.s.ocr_accurate_model)
            if ex2.rank() > ex.rank():
                ctx, ex, scores = ctx2, ex2, scores2
            t = lap("ocr_accurate", t)

        result_type = ex.document_type
        fields = dict(ex.fields)
        sources = dict(ex.field_sources)
        warnings = list(ex.warnings)
        country = fields.get("issuing_country", "")
        llm_used = False
        llm_usage = None

        wants_llm = llm_mode != "off" and (
            llm_mode == "always" or not ex.complete or ex.llm_preferred or ex.document_type == "unknown"
        )
        if wants_llm and not llm_ready:
            warnings.append("Fallback LLM non configuré (ANTHROPIC_API_KEY absente) : résultat issu des règles locales uniquement")
        elif not wants_llm and not ex.complete:
            warnings.append("Extraction partielle et fallback LLM désactivé")
        if wants_llm and llm_ready:
            try:
                imgs = [p.image for p in pages if p.image is not None]
                res = await self.llm.extract(imgs, ctx.text, ex.document_type, ex.fields)
                llm_used = True
                llm_usage = res["usage"]
                if result_type == "unknown" or (not forced and ex.document_type != res["document_type"] and not ex.checks):
                    result_type = res["document_type"]
                country = country or res["country"]
                for k, v in res["fields"].items():
                    if k in fields and sources.get(k) in PROTECTED_SOURCES and ex.checks_ok:
                        if _norm_cmp(fields[k]) != _norm_cmp(v):
                            warnings.append(f"LLM diverge sur '{k}' ({v!r}) - valeur validée par checksum conservée")
                        continue
                    if k in fields and _norm_cmp(fields[k]) != _norm_cmp(v):
                        warnings.append(f"'{k}' corrigé par le LLM ({fields[k]!r} -> {v!r})")
                    if k not in fields or _norm_cmp(fields[k]) != _norm_cmp(v):
                        fields[k] = v
                        sources[k] = "llm"
            except LlmError as e:
                warnings.append(f"Fallback LLM indisponible : {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("LLM fallback failed")
                warnings.append(f"Fallback LLM en erreur : {type(e).__name__}")
            t = lap("llm", t)

        missing = [k for k in ex.required if not fields.get(k)] if result_type == ex.document_type else []
        if not missing and ex.checks and ex.checks_ok:
            status = "verified"
        elif not missing and (ex.document_type != "unknown" or llm_used):
            status = "complete"
        else:
            status = "partial"

        timings["total"] = round((time.perf_counter() - t0) * 1000)
        out = {
            "document_type": result_type,
            "document_label": TYPES.get(result_type, result_type),
            "country": country or None,
            "status": status,
            "fields": fields,
            "field_sources": sources,
            "checks": ex.checks,
            "missing_fields": missing,
            "warnings": warnings,
            "entities": entities(ctx.text),
            "engine": {
                "input": kind,
                "pages": len(pages),
                "ocr_passes": passes,
                "llm_used": llm_used,
                "llm_model": self.s.llm_model if llm_used else None,
                "llm_usage": llm_usage,
                "classification_scores": {k: v for k, v in scores.items() if v},
            },
            "timings_ms": timings,
        }
        if ex.extra:
            out.update(ex.extra)
        if opts.include_text:
            out["text"] = ctx.text
        return out


def _norm_cmp(v) -> str:
    if isinstance(v, list):
        v = ",".join(map(str, v))
    return "".join(ch for ch in norm(str(v)) if ch.isalnum())

