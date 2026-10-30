"""Owner model discovery serializes an explicit safe view, never provider credentials."""

from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.models import FrozenModel, Namespace, NonNegative
from domain_kg.providers.configuration import ModelSetup
from domain_kg.providers.health import ConnectionCheck, ProviderChecks
from domain_kg.providers.presets import PRESETS, ModelPreset
from domain_kg.providers.profiles import ProviderProfile
from domain_kg.providers.service import ProviderService


class ModelChoice(FrozenModel):
    profile_id: UUID
    name: str
    extraction_model: str
    embedding_model: str
    embedding_dimensions: int
    supports_images: bool
    approved_for_kg: bool
    profile_revision: NonNegative
    extraction_context_tokens: int
    embedding_context_tokens: int
    max_completion_tokens: int


class ModelConnectionView(FrozenModel):
    endpoint: str
    api_format: str
    has_key: bool
    embedding_endpoint: str
    embedding_has_key: bool
    response_format: str
    thinking: bool | None
    image_input_tokens: int
    embedding_send_dimensions: bool
    timeout_seconds: int
    allow_restricted: bool


class ModelSettings(Namespace):
    choices: tuple[ModelChoice, ...]
    selected_profile_id: UUID | None
    selection_revision: NonNegative
    default_profile_id: UUID | None
    can_configure_connections: bool
    connection: ModelConnectionView | None
    presets: tuple[ModelPreset, ...]
    last_check: ConnectionCheck | None = None
    warnings: tuple[ErrorCode, ...] = ()


def choice(profile: ProviderProfile, namespace: Namespace) -> ModelChoice:
    return ModelChoice(
        profile_id=profile.id,
        name=profile.name or profile.extraction_model,
        extraction_model=profile.extraction_model,
        embedding_model=profile.embedding_model,
        embedding_dimensions=profile.embedding_dimensions,
        supports_images=profile.supports_images,
        approved_for_kg=namespace in profile.allowed_namespaces,
        profile_revision=profile.revision,
        extraction_context_tokens=profile.extraction_context_tokens,
        embedding_context_tokens=profile.embedding_context_tokens,
        max_completion_tokens=profile.max_completion_tokens,
    )


async def inspect_models(
    providers: ProviderService, access: AccessContext, kg: UUID
) -> ModelSettings:
    current = await providers.policy.revalidate(access, action=Action.CONFIGURE)
    if kg not in current.kg_ids:
        from domain_kg.errors import inaccessible_reference

        raise inaccessible_reference()
    providers.registry.refresh()
    namespace = Namespace(org_id=current.org_id, kg_id=kg)
    can_configure = current.platform_operator and providers.registry.config_path is not None
    profiles = [
        p
        for p in providers.registry.profiles.values()
        if p.data_policy_approved
        and p.embedding_dimensions == providers.router.dimensions
        and (
            namespace in p.allowed_namespaces
            or (can_configure and any(n.org_id == current.org_id for n in p.allowed_namespaces))
        )
    ]
    if len(profiles) > 100:
        raise DomainError(
            ErrorCode.QUOTA_EXCEEDED, "Model inventory exceeds its 100-profile bound."
        )
    try:
        selected = await providers.selected(current, kg)
    except DomainError as error:
        if error.code != ErrorCode.PROVIDER_UNAVAILABLE:
            raise
        selected = None
    connection = None
    last_check = None
    warnings: list[ErrorCode] = []
    if can_configure and selected:
        await ModelSetup(providers).authorize(current, kg)
        try:
            profile, chat = providers.registry.resolve(
                UUID(selected["profile_id"]), namespace, restricted=False
            )
            _, embedding = providers.registry.resolve_embedding(
                profile.id, namespace, restricted=False
            )
            connection = ModelConnectionView(
                endpoint=chat.endpoint,
                api_format=chat.api_format,
                has_key=bool(chat.api_key.get_secret_value()),
                embedding_endpoint=embedding.endpoint,
                embedding_has_key=bool(embedding.api_key.get_secret_value()),
                response_format=profile.extraction_response_format,
                thinking=profile.extraction_enable_thinking,
                image_input_tokens=profile.image_input_tokens,
                embedding_send_dimensions=profile.embedding_send_dimensions,
                timeout_seconds=profile.timeout_seconds,
                allow_restricted=profile.allow_restricted,
            )
        except DomainError as error:
            if error.code not in (
                ErrorCode.PROVIDER_UNAVAILABLE,
                ErrorCode.RESOURCE_NOT_ACCESSIBLE,
            ):
                raise
            warnings.append(error.code)
        else:
            try:
                last_check = await ProviderChecks(providers).last(current, kg)
            except DomainError as error:
                if error.code not in (ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.VERSION_CONFLICT):
                    raise
    identifiers = {p.id for p in profiles}
    await providers.policy.revalidate(current)
    return ModelSettings(
        org_id=current.org_id,
        kg_id=kg,
        choices=tuple(choice(p, namespace) for p in profiles),
        selected_profile_id=UUID(selected["profile_id"]) if selected else None,
        selection_revision=selected["revision"] if selected else 0,
        default_profile_id=providers.registry.default_profile_id
        if providers.registry.default_profile_id in identifiers
        else None,
        can_configure_connections=can_configure,
        connection=connection,
        presets=PRESETS,
        last_check=last_check,
        warnings=tuple(warnings),
    )
