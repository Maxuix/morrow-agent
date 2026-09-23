"""One-shot in-container Morrow bootstrap for benchmark trials.

Registers the task workspace and a provider/model pair without any interactive
prompt and *without storing secrets*: the provider is created with
``secret=None``, so Morrow resolves the credential at runtime from the
``MORROW_<PROVIDER_ID>_API_KEY`` environment variable (per-exec env only).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from morrow.bootstrap import build_application
from morrow.core.models import ModelCapabilityOverrides


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--api-model-id", required=True)
    args = parser.parse_args()

    app = build_application(state_root=Path(args.state_root))

    resolution = app.workspace_service.resolve(Path(args.workspace))
    if resolution.status == "candidate":
        identity = app.workspace_service.confirm(resolution)
        print(f"workspace registered: {identity.workspace_id}")
    else:
        print(f"workspace exists: {resolution.identity.workspace_id}")

    providers = app.provider_service.list().providers
    if args.provider_id not in providers:
        app.provider_service.add_provider(
            args.provider_id,
            adapter_id=args.adapter,
            base_url=args.base_url,
            secret=None,
        )
        print(f"provider added: {args.provider_id} (secret=None, env credential)")
    else:
        print(f"provider exists: {args.provider_id}")

    provider = app.provider_service.list().providers[args.provider_id]
    if args.model_id not in provider.models:
        app.provider_service.add_model(
            args.provider_id,
            args.model_id,
            api_model_id=args.api_model_id,
            capabilities=ModelCapabilityOverrides(
                reasoning_efforts=app.registry.capabilities(args.adapter).reasoning_efforts,
            ),
        )
        print(f"model added: {args.provider_id}/{args.model_id}")
    else:
        print(f"model exists: {args.provider_id}/{args.model_id}")

    app.provider_service.use_model(args.provider_id, args.model_id)
    print(f"active model: {args.provider_id}/{args.model_id}")


if __name__ == "__main__":
    main()
