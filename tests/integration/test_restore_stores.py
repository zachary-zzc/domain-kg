"""Only a live restore job can provision an inactive, explicitly approved target."""

from uuid import uuid4

import pytest
from pydantic import SecretStr
from qdrant_client import models

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RestoreCommand
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
)

pytestmark = pytest.mark.storage


async def test_inactive_store_binding_requires_live_job_and_new_provider_approval(
    material_service, team, catalog
):
    from domain_kg.operations.restore_stores import RestoreStores

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.RESTORE)
    operations = OwnerOperations(queue)
    backup = await operations.submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="inactive-bind-backup"
        ),
    )
    await BackupService(queue).run(await queue.claim("backup", kinds=("backup",)))
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="synthetic",
        extraction_model="unused",
        embedding_model="synthetic-four",
        embedding_dimensions=4,
        data_policy_approved=True,
        allow_restricted=True,
        allowed_namespaces=(Namespace(org_id=owner.org_id, kg_id=kg),),
    )
    providers = ProviderRegistry(
        ProviderConfig(
            profiles=(profile,),
            connections=(
                ProviderConnection(
                    ref="synthetic", endpoint="http://127.0.0.1:1/v1", api_key=SecretStr("fixture")
                ),
            ),
        )
    )
    receipt = await operations.submit(
        owner,
        RestoreCommand(
            kg_id=kg,
            expected_published_revision=0,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Isolated target",
            idempotency_key="inactive-bind-restore",
        ),
    )
    job = await queue.claim("restore", kinds=("restore",))
    target = await RestoreTargets(queue).get(owner, kg, receipt.job_id)
    binder = RestoreStores(queue, providers)
    # Source approval never authorizes data use in the newly allocated target.
    with pytest.raises(DomainError):
        await binder.provision(job)
    providers.profiles[profile.id] = profile.model_copy(
        update={"allowed_namespaces": (target.target,)}
    )
    binding = None
    try:
        binding = await binder.provision(job)
        assert binding.target == target
        assert binding.stores.namespace == target.target
        assert binding.stores.authority.namespace == target.target
        assert binding.stores.graph.namespace == target.target
        assert binding.stores.vectors.namespace == target.target
        assert binding.stores.blobs.namespace == target.target
        assert binding.embedding.kg_id == target.target.kg_id and binding.embedding.dimensions == 4
        info = await binding.stores.vectors.client.get_collection(binding.stores.vectors.collection)
        assert isinstance(info.config.params.vectors, models.VectorParams)
        assert info.config.params.vectors.size == 4
        with pytest.raises(DomainError):
            await queue.router.for_kg(owner, target.target.kg_id)
        assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
        assert (
            await queue.database.kg_grants.count_documents({"kg_id": str(target.target.kg_id)}) == 0
        )
        providers.profiles[profile.id] = profile
        with pytest.raises(DomainError):
            await binder.provision(job)
        providers.profiles[profile.id] = profile.model_copy(
            update={"allowed_namespaces": (target.target,)}
        )
        await queue.cancel(owner, receipt.job_id, idempotency_key="stop-inactive-bind")
        with pytest.raises(DomainError):
            await binder.provision(job)
    finally:
        if binding is not None:
            await binding.stores.vectors.client.delete_collection(binding.stores.vectors.collection)
