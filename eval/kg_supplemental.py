"""Explicit late model addition, leaving the original seven-model freeze intact."""

from eval.kg_comparison import CLOUD, SMALL
from eval.source_role_eval import digest
from eval.source_role_run import read


def models(root):
    policy = root / "api-only-policy.json"
    if policy.exists():
        record = read(policy)
        expected = [SMALL[0], *CLOUD]
        if record["active_models"] != expected or record["local_inference_allowed"]:
            raise ValueError("API-only execution policy changed")
        extra = root / "cloudflare-supplement-frozen.json"
        if extra.exists():
            from eval.kg_cloudflare import MODELS, prepare

            supplement = read(extra)
            prepare(root, supplement["account_id"])
            if (root / "cloudflare-schema-frozen.json").exists():
                from eval.kg_cloudflare_schema import MODELS as SCHEMA_MODELS
                from eval.kg_cloudflare_schema import prepare as prepare_schema

                prepare_schema(root)
                return (*expected, *MODELS, *SCHEMA_MODELS)
            return (*expected, *MODELS)
        return tuple(expected)
    path = root / "supplemental-model-frozen.json"
    extra = []
    if path.exists():
        record = read(path)
        if record["original_manifest_sha256"] != digest(read(root / "frozen.json")):
            raise ValueError("supplemental model uses another source protocol")
        extra = record["models"]
        if extra != ["local-qwen3.5-0.8b-q4"]:
            raise ValueError("unrecognized supplemental model")
    return (*extra, *SMALL, *CLOUD)
