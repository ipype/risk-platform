"""Seed the RBS taxonomy. Idempotent -- safe to run more than once.

Run inside the api container:
    docker compose exec api python -m app.seed_rbs
"""
import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.rbs import RbsCategory, RbsSubcategory

# (category_code, category_name, [(subcode, subname), ...])
RBS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("ENV", "Environmental", [
        ("010", "Environmental document completion"),
        ("020", "Environmental site assessment issues"),
        ("030", "Environmental permitting"),
        ("040", "Archaeological discoveries / heritage properties"),
        ("050", "Contamination, hazardous materials, Designated Substances"),
        ("060", "Wetlands / stream / habitat mitigation / Species-at-Risk"),
        ("070", "Stormwater, hydraulic requirements"),
        ("080", "Environmental impacts during construction"),
        ("090", "Noise and vibration"),
        ("900", "Other environmental issues"),
    ]),
    ("STG", "Structural & Geotechnical Design", [
        ("010", "Potential changes to structures design (bridges, superstructures, retaining walls, etc.)"),
        ("020", "Potential changes to geotechnical design (foundations, liquefaction, mitigation) or challenging geotechnical conditions"),
        ("030", "Changes to structural design criteria"),
        ("900", "Other structures and geotechnical issues"),
    ]),
    ("DES", "Design", [
        ("010", "Potential changes to design"),
        ("020", "Approval of design deviations or changes to design criteria"),
        ("030", "Projects by third-parties affected by or affecting this project (design coordination)"),
        ("040", "Potential changes to S&TCS"),
        ("050", "Additional scope driven by internal considerations"),
        ("900", "Other design issues"),
    ]),
    ("UTL", "Utility", [
        ("010", "Utility design coordination and agreements"),
        ("020", "Utility relocations and conflicts"),
        ("900", "Other utility issues"),
    ]),
    ("RWA", "Railway Access", [
        ("010", "Issues associated with development of Rail Corridor Access Plan"),
        ("020", "Uncertainty in future access costs"),
        ("030", "Limitations in access"),
        ("040", "Unexpected access needs"),
        ("900", "Other railway access issues"),
    ]),
    ("PSP", "Partnerships & Stakeholders", [
        ("010", "Indigenous Nations issues"),
        ("020", "Public involvement issues"),
        ("030", "Third-party issues"),
        ("900", "Other partnership & stakeholder issues"),
    ]),
    ("MGT", "Management & Funding", [
        ("010", "Change in project managers or Key Individuals"),
        ("020", "Delayed decision-making"),
        ("030", "Availability of funding / cash flow issues"),
        ("040", "Political changes"),
        ("050", "Provincial government labour limitations"),
        ("900", "Other management & funding issues"),
    ]),
    ("CTR", "Contracting & Procurement", [
        ("010", "Change in project delivery method"),
        ("020", "Issues related to contract language (warranties, liquidated damages, insurance, etc.)"),
        ("030", "Delays in procurement processes"),
        ("040", "Market conditions"),
        ("050", "Delays in procurement of specialty materials or equipment and associated cost premiums"),
        ("060", "Contractor non-performance"),
        ("070", "Availability of labour / productivity disruptions"),
        ("080", "Testing and system integration"),
        ("900", "Other contracting & procurement issues"),
    ]),
    ("CNS", "Construction", [
        ("010", "Staging issues"),
        ("020", "Construction permitting issues"),
        ("030", "Work windows (e.g. weather, ecological)"),
        ("040", "Construction schedule uncertainty"),
        ("050", "Over-water construction issues"),
        ("060", "Earthwork issues (reuse, haul, disposal, etc.)"),
        ("070", "Coordination with adjacent projects"),
        ("080", "Contractor access, staging coordination, constructability issues"),
        ("090", "Construction accidents"),
        ("900", "Other construction issues"),
    ]),
    ("OTH", "Other", [
        ("900", "Other risks"),
    ]),
]


async def main() -> None:
    created_cats = 0
    created_subs = 0
    async with AsyncSessionLocal() as session:
        for order, (code, name, subs) in enumerate(RBS, start=1):
            found = await session.execute(
                select(RbsCategory).where(RbsCategory.code == code)
            )
            category = found.scalar_one_or_none()
            if category is None:
                category = RbsCategory(code=code, name=name, sort_order=order)
                session.add(category)
                await session.flush()  # assigns category.id
                created_cats += 1

            for sub_order, (subcode, subname) in enumerate(subs, start=1):
                sub_found = await session.execute(
                    select(RbsSubcategory).where(
                        RbsSubcategory.category_id == category.id,
                        RbsSubcategory.code == subcode,
                    )
                )
                if sub_found.scalar_one_or_none() is None:
                    session.add(
                        RbsSubcategory(
                            category_id=category.id,
                            code=subcode,
                            name=subname,
                            sort_order=sub_order,
                        )
                    )
                    created_subs += 1

        await session.commit()

    total_cats = len(RBS)
    total_subs = sum(len(s) for _, _, s in RBS)
    print(
        f"Seed complete. Categories: {total_cats} total ({created_cats} new). "
        f"Subcategories: {total_subs} total ({created_subs} new)."
    )


if __name__ == "__main__":
    asyncio.run(main())