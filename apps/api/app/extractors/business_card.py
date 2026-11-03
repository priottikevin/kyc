"""Business cards: contact entities by pattern; name/company/title are best resolved by the LLM."""

import re

from ..textutils import EMAIL, PHONE, POSTCODE_CITY, URL, entities
from .base import Ctx, Extraction

TITLE_WORDS = r"\b(CEO|CTO|CFO|COO|DIRECT|MANAGER|RESPONSABLE|GERANT|FONDATEUR|FOUNDER|PRESIDENT|CONSULTANT|INGENIEUR|ENGINEER|COMMERCIAL|CHARGE|ASSOCIE|PARTNER|AVOCAT|DOCTEUR|DR\b|ARCHITECTE|CHEF|HEAD|LEAD|DEVELOPPEUR|DEVELOPER|ASSISTANT|AGENT|CONSEILLER|EXPERT|SALES|MARKETING)"


def extract(ctx: Ctx, doc_type: str) -> Extraction:
    ex = Extraction("business_card", required=["full_name"])
    ex.llm_preferred = True
    ents = entities(ctx.text)
    ex.set("emails", ents.get("emails"))
    ex.set("phones", ents.get("phones"))
    ex.set("websites", ents.get("websites"))
    ex.set("siren_siret", ents.get("siren_siret"))
    m = POSTCODE_CITY.search(ctx.text)
    if m:
        # address = the row holding the postcode plus the row just above if it looks like a street
        for i, row in enumerate(ctx.row_texts):
            if m.group(1) in row:
                street = ctx.row_texts[i - 1] if i and re.search(r"^\d+|RUE|AVENUE|BD|BOULEVARD|CHEMIN|PLACE|ALLEE|ROUTE|IMPASSE", ctx.row_texts[i - 1].upper()) else ""
                ex.set("address", ", ".join(x for x in (street, row) if x))
                break

    # Name heuristic: tallest text row that is 2-4 alphabetic words and not a contact line
    best = None
    for row in ctx.rows:
        t = " ".join(l.text for l in row).strip()
        if EMAIL.search(t) or PHONE.search(t) or URL.search(t) or re.search(r"\d", t):
            continue
        words = t.split()
        if not 2 <= len(words) <= 4 or re.search(TITLE_WORDS, t.upper()):
            continue
        if not all(re.fullmatch(r"[A-Za-zÀ-ÿ'\-.]+", w) for w in words):
            continue
        h = max(l.h for l in row)
        if best is None or h > best[0]:
            best = (h, t)
    if best:
        ex.set("full_name", best[1], "heuristic")
    for t in ctx.row_texts:
        if re.search(TITLE_WORDS, t.upper()) and not EMAIL.search(t):
            ex.set("job_title", t.strip(), "heuristic")
            break
    return ex
