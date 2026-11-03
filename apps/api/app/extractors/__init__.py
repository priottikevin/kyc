from . import business, business_card, driving_license, identity, insurance, vehicle_registration
from .base import Ctx, Extraction


def run(doc_type: str, ctx: Ctx) -> Extraction:
    if doc_type in business.EXTRACTORS:
        return business.EXTRACTORS[doc_type](ctx)
    if doc_type in ("id_card", "passport", "residence_permit"):
        return identity.extract(ctx, doc_type)
    if doc_type == "driving_license":
        return driving_license.extract(ctx, doc_type)
    if doc_type == "vehicle_registration":
        return vehicle_registration.extract(ctx, doc_type)
    if doc_type == "insurance_certificate":
        return insurance.extract(ctx, doc_type)
    if doc_type == "business_card":
        return business_card.extract(ctx, doc_type)
    ex = Extraction("unknown", llm_preferred=True)
    return ex


__all__ = ["Ctx", "Extraction", "run"]
