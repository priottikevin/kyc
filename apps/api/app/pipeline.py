"""Orchestration: ingest -> fast OCR -> classify -> rules -> (accurate OCR) -> (Claude fallback)."""

import asyncio
import logging
import time
from difflib import SequenceMatcher
from dataclasses import dataclass

from . import extractors, ingest
from .confidence import field_confidence, uncertain
from .classify import TYPES, classify
from .config import Settings
from .extractors import Ctx, Extraction
from .llm import LlmClient, LlmError
from .ocr import Line, OcrEngines, line_crop, native_to_lines, ocr_page, reread_line
from .textutils import entities, norm

log = logging.getLogger(__name__)

# fields whose local value is trusted over the LLM (checksum-validated)
PROTECTED_SOURCES = {"mrz", "mrz+ocr"}
# name fields re-read with the accurate model when they come from the fast pass without MRZ confirmation
REFINE_FIELDS = ("surname", "given_names", "usage_name", "address")
IDENTITY_TYPES = {"id_card", "passport", "residence_permit", "driving_license"}
BUSINESS_TYPES = set(extractors.business.EXTRACTORS)
DENSE_LINES = 70


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

    def _ocr_pages(self, pages: list[ingest.Page], size: str) -> tuple[list[Line], list[tuple]]:
        """OCR all pages; stack them vertically so layout helpers keep working across pages.

        Also returns, per OCR'd page, (image the boxes refer to, y offset, y end) for targeted re-reads.
        """
        out: list[Line] = []
        views: list[tuple] = []
        y_off = 0.0
        for p in pages:
            if p.is_native:
                lines = native_to_lines(p.native_lines)
            else:
                lines, img = ocr_page(self.engines.get(size), p.image)
                views.append((img, y_off, y_off + img.shape[0]))
            for ln in lines:
                out.append(Line(ln.text, ln.conf, ln.x0, ln.y0 + y_off, ln.x1, ln.y1 + y_off, ln.chars))
            if lines:
                y_off = max(l.y1 for l in out) + 200
        return out, views

    async def _ocr(self, pages, size) -> tuple[list[Line], list[tuple]]:
        async with self.sem:
            return await asyncio.to_thread(self._ocr_pages, pages, size)

    def _reread(self, lines: list[Line], views: list[tuple], targets: list[Line]) -> list[Line]:
        eng = self.engines.get(self.s.ocr_accurate_model)
        repl: dict[int, Line] = {}
        for ln in targets:
            for img, y0, y1 in views:
                if y0 <= ln.cy < y1:
                    new = reread_line(eng, img, ln, y0, lines)
                    # guard against a crop that caught a neighbour: the re-read must stay close to the original
                    if new and SequenceMatcher(None, _norm_cmp(ln.text), _norm_cmp(new.text)).ratio() >= 0.75:
                        repl[id(ln)] = new
                    break
        return [repl.get(id(ln), ln) for ln in lines]

    async def _refine_names(self, lines, views, ex: Extraction, forced):
        """Names have no checksum without an MRZ: re-read just those lines with the accurate model (~20 ms each)."""
        keys = [_norm_cmp(ex.fields[k]) for k in REFINE_FIELDS if ex.field_sources.get(k) == "ocr" and ex.fields.get(k)]
        # a line is a target when it holds the value, or is one line of a multi-line value (address)
        targets = [
            ln for ln in lines
            if (t := _norm_cmp(ln.text)) and any(k and (k in t or (len(t) >= 4 and t in k)) for k in keys)
        ]
        if not targets:
            return None
        async with self.sem:
            new_lines = await asyncio.to_thread(self._reread, lines, views, targets)
        return new_lines, self._analyse(new_lines, forced)

    def _crops(self, lines: list[Line], views: list[tuple], targets: list[Line]) -> list[tuple[str, object, Line]]:
        out = []
        for n, ln in enumerate(targets):
            for img, y0, y1 in views:
                if y0 <= ln.cy < y1:
                    crop = line_crop(img, ln, y0, lines)
                    if crop is not None:
                        out.append((f"L{n}", crop, ln))
                    break
        return out

    async def _llm_check(self, lines, views, conf, where, forced):
        """Send only the lines holding doubtful values to the LLM; corrected lines go back through the rules.

        Returns (lines, ctx, ex, scores, ids of the lines the LLM read, usage) or None.
        """
        keys = [k for k in uncertain(conf) if k in where]
        targets = list({id(l): l for k in keys for l in where[k]}.values())[:12]
        if not targets:
            return None
        crops = await asyncio.to_thread(self._crops, lines, views, targets)
        if not crops:
            return None
        res = await self.llm.read_lines([(key, img, ln.text) for key, img, ln in crops])
        repl, checked = {}, set()
        for key, _, ln in crops:
            txt = res["fields"].get(key)
            if not txt:
                continue
            if _norm_cmp(txt) == _norm_cmp(ln.text):
                new = Line(ln.text, ln.conf, ln.x0, ln.y0, ln.x1, ln.y1, ln.chars)  # same reading
            else:
                new = Line(txt, 1.0, ln.x0, ln.y0, ln.x1, ln.y1, None)
            repl[id(ln)] = new
            checked.add(id(new))
        if not repl:
            return None
        new_lines = [repl.get(id(l), l) for l in lines]
        ctx2, ex2, scores2 = self._analyse(new_lines, forced)
        return new_lines, ctx2, ex2, scores2, checked, res["usage"]

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
        lines, views = await self._ocr(pages, self.s.ocr_fast_model)
        passes.append("native" if not has_images else self.s.ocr_fast_model)
        ctx, ex, scores = self._analyse(lines, forced)
        t = lap("ocr_fast", t)
        from_fast = True

        llm_mode = opts.llm or self.s.llm_mode
        llm_ready = self.llm is not None and llm_mode != "off"
        # Second, more accurate OCR pass when rules are not satisfied (skip if the LLM will look at the image anyway).
        # Not on dense text pages (invoices, reports...): several seconds for little gain, the fast model reads print well.
        dense = len(lines) > DENSE_LINES or ex.document_type in BUSINESS_TYPES
        if (not ex.complete and has_images and self.s.ocr_accurate_model != "none" and not dense
                and not (ex.llm_preferred and llm_ready)):
            lines2, views2 = await self._ocr(pages, self.s.ocr_accurate_model)
            ctx2, ex2, scores2 = self._analyse(lines2, forced)
            passes.append(self.s.ocr_accurate_model)
            if ex2.rank() > ex.rank():
                ctx, ex, scores = ctx2, ex2, scores2
                lines, views, from_fast = lines2, views2, False
            t = lap("ocr_accurate", t)

        if (from_fast and views and self.s.ocr_accurate_model != "none"
                and self.s.ocr_accurate_model != self.s.ocr_fast_model and ex.document_type in IDENTITY_TYPES):
            refined = await self._refine_names(lines, views, ex, forced)
            if refined:  # None when nothing needed a re-read (e.g. names confirmed by the MRZ)
                if refined[1][1].rank() >= ex.rank():
                    lines, (ctx, ex, scores) = refined
                    passes.append(f"{self.s.ocr_accurate_model}:names")
                t = lap("ocr_names", t)

        llm_used = False
        llm_usage = None
        llm_kind = None
        pre_warnings: list[str] = []
        conf, where = field_confidence(ex.fields, ex.field_sources, ex.checks, lines)
        checked: set[int] = set()

        wants_llm = llm_mode != "off" and (
            llm_mode == "always" or not ex.complete or ex.llm_preferred or ex.document_type == "unknown"
        )
        # Targeted check: the document is understood, only some values hold a doubtful character
        if (not wants_llm and llm_ready and self.s.llm_check_uncertain and views
                and any(k in where for k in uncertain(conf))):
            try:
                chk = await self._llm_check(lines, views, conf, where, forced)
                if chk:
                    new_lines, ctx2, ex2, scores2, checked, llm_usage = chk
                    llm_used, llm_kind = True, "lines"
                    if ex2.rank() >= ex.rank():
                        for k in ex2.fields:
                            if _norm_cmp(ex2.fields[k]) != _norm_cmp(ex.fields.get(k, "")):
                                if k in ex.fields:
                                    pre_warnings.append(f"'{k}' corrigé par le LLM ({ex.fields[k]!r} -> {ex2.fields[k]!r})")
                                ex2.field_sources[k] = "llm"
                        lines, ctx, ex, scores = new_lines, ctx2, ex2, scores2
                    else:
                        checked = set()
            except LlmError as e:
                pre_warnings.append(f"Vérification LLM indisponible : {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("LLM line check failed")
                pre_warnings.append(f"Vérification LLM en erreur : {type(e).__name__}")
            t = lap("llm", t)

        result_type = ex.document_type
        fields = dict(ex.fields)
        sources = dict(ex.field_sources)
        warnings = list(ex.warnings) + pre_warnings
        country = fields.get("issuing_country", "")

        if wants_llm and not llm_ready:
            warnings.append("Fallback LLM non configuré (ANTHROPIC_API_KEY absente) : résultat issu des règles locales uniquement")
        elif not wants_llm and not ex.complete:
            warnings.append("Extraction partielle et fallback LLM désactivé")
        if wants_llm and llm_ready:
            try:
                imgs = [p.image for p in pages if p.image is not None]
                res = await self.llm.extract(imgs, ctx.text, ex.document_type, ex.fields)
                llm_used, llm_kind = True, "document"
                llm_usage = res["usage"]
                if res["document_type"] != "other" and (
                        result_type == "unknown" or (not forced and ex.document_type != res["document_type"] and not ex.checks)):
                    result_type = res["document_type"]
                country = country or res["country"]
                for k, v in res["fields"].items():
                    if k in fields and sources.get(k) in PROTECTED_SOURCES and ex.checks_ok:
                        if _norm_cmp(fields[k]) != _norm_cmp(v):
                            warnings.append(f"LLM diverge sur '{k}' ({v!r}) - valeur validée par checksum conservée")
                        continue
                    if k in fields and _norm_cmp(fields[k]) == _norm_cmp(v):
                        if sources.get(k) not in PROTECTED_SOURCES:
                            sources[k] = "ocr+llm"
                        continue
                    if k in fields:
                        warnings.append(f"'{k}' corrigé par le LLM ({fields[k]!r} -> {v!r})")
                    fields[k] = v
                    sources[k] = "llm"
            except LlmError as e:
                warnings.append(f"Fallback LLM indisponible : {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("LLM fallback failed")
                warnings.append(f"Fallback LLM en erreur : {type(e).__name__}")
            t = lap("llm", t)

        conf, where = field_confidence(fields, sources, ex.checks, lines)
        for k, c in conf.items():
            # OCR and LLM read the same value independently, or the LLM re-read every line it comes from
            if sources.get(k) == "ocr+llm" or (k in where and c["level"] in ("check", "unknown")
                                               and all(id(l) in checked for l in where[k])):
                c["level"] = "confirmed"

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
            "field_confidence": conf,
            "checks": ex.checks,
            "missing_fields": missing,
            "warnings": warnings,
            "entities": entities(ctx.text),
            "engine": {
                "input": kind,
                "pages": len(pages),
                "ocr_passes": passes,
                "llm_used": llm_used,
                "llm_kind": llm_kind,
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

