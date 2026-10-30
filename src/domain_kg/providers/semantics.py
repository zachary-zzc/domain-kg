"""Versioned source-role guidance; instructions are not a semantic truth validator."""

from collections.abc import Iterable

from domain_kg.providers.profiles import ProviderProfile

# These describe existing vocabulary; they do not add predicates to a KG schema.
RELATION_MEANINGS = {
    "is_a": "instance/subtype -> class, not a mere association",
    "part_of": "component/member region -> containing whole",
    "located_in": "located entity -> geographic location",
    "born_in": "person -> birthplace, not a residence or performance location",
    "born_on": "person -> birth date, never a naturalization, appointment or release date",
    "died_on": "person -> death date",
    "created_by": (
        "work/invention -> creator; writing, composition and development can qualify; "
        "performer, publisher, owner or subject credits alone do not establish authorship"
    ),
    "operated_by": "operated entity -> operator, not automatically its founder or owner",
    "uses": "user -> tool/resource used, never a substitute for authorship or association",
    "member_of": "member -> organization/group",
    "affiliated_with": "entity -> explicitly affiliated organization",
    "founded_on": "organization/settlement -> founding date, not later merger by default",
    "released_on": "work/product -> public release date, not writing or recording date",
    "occurred_on": "event -> occurrence date",
    "participated_in": "participant -> event/activity; does not imply portraying a character",
    "won": "winner -> award/competition won",
    "has_value": "quantity/property -> value with its units and conditions preserved",
    "precedes": "earlier event/entity -> later event/entity in the stated ordering",
    "causes": "explicit cause -> effect; correlation and temporal order are insufficient",
    "supports": "supporter/evidence -> supported entity/claim; preserve the kind and scope",
    "opposes": "opponent -> opposed entity/claim with the stated scope",
    "contradicts": "claim/evidence -> incompatible claim under the same conditions",
    "produces": "producer -> product, including passive wording 'X is made by Y' as Y -> X",
    "screens_for": "test/procedure -> screened condition",
    "measures": "test/instrument -> measured quantity",
    "associated_with": "stated association, without implying causation or direction of effect",
    "interacts_with": "stated interaction between endpoints, not mere co-occurrence",
    "inhibits": "inhibitor -> inhibited entity/process",
    "activates": "activator -> activated entity/process",
    "related_to": "explicit meaningful relationship only, never co-occurrence or a tautology",
}


def role_instructions(predicates: Iterable[str]) -> str:
    meanings = "\n".join(
        f"{name}: {RELATION_MEANINGS[name]}."
        for name in dict.fromkeys(predicates)
        if name in RELATION_MEANINGS
    )
    return (
        "\nPolicy source_roles_v1. Interpret every relation as subject -> target. "
        "Read the complete supplied source to resolve who did what to whom. "
        "Check active/passive direction and distinguish author, performer, publisher, "
        "portrayed character and owner. A shared name or co-occurrence is not a claim. "
        "Resolve pronouns only when the source makes the referent unambiguous. "
        "Keep the condition, time and location needed to make each claim accurate as "
        "source-backed qualifiers. A past or conditional claim must not become timeless. "
        "Apply polarity to this claim, not an unrelated negation elsewhere in the passage. "
        "Do not replace a missing predicate with a misleading available predicate; omit "
        "unsupported relations. Preserve distinct supported facts, but do not manufacture "
        "bridges or merge people with different roles. Use no outside knowledge. "
        "Quoting the source alone does not establish that the chosen relation follows. "
        "Before output, check endpoint roles, predicate meaning, polarity and qualifiers "
        "against the source. These definitions apply only to supplied predicates:\n" + meanings
    )


def profile_cache_identity(profile: ProviderProfile) -> str:
    """Do not invalidate legacy checkpoints merely by adding the optional setting."""
    exclude = {"extraction_semantics"} if profile.extraction_semantics == "legacy" else set()
    return profile.model_dump_json(exclude=exclude)
