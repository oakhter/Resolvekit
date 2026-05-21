from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEFAULT_DATABASE_URL = "postgresql://resolvekit:resolvekit@localhost:5432/resolvekit"
DEFAULT_DEMO_TICKET = "Customer cannot sign in on mobile app after a role change. Desktop works, mobile shows 403."


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "example_product"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing_lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def provider_key_name(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in {"openai", "gemini"}:
        raise ValueError("Provider must be openai or gemini")
    return "OPENAI_API_KEY" if normalized == "openai" else "GEMINI_API_KEY"


def build_env_updates(
    existing: dict[str, str],
    provider: str,
    provider_key: str,
    demo: bool,
) -> dict[str, str]:
    provider = provider.strip().lower()
    key_name = provider_key_name(provider)
    other_key = "GEMINI_API_KEY" if key_name == "OPENAI_API_KEY" else "OPENAI_API_KEY"
    viewer_token = existing.get("API_KEY") if existing.get("API_KEY") not in {"", "change-me", None} else f"rk_viewer_{secrets.token_urlsafe(18)}"
    admin_token = (
        existing.get("CONFIGURATOR_API_KEY")
        if existing.get("CONFIGURATOR_API_KEY") not in {"", "change-me-configurator", None}
        else f"rk_admin_{secrets.token_urlsafe(18)}"
    )
    return {
        "ACTIVE_PROVIDER": provider,
        "DEMO_MODE": "true" if demo else "false",
        key_name: provider_key.strip(),
        other_key: existing.get(other_key, ""),
        "API_KEY": viewer_token,
        "CONFIGURATOR_API_KEY": admin_token,
        "VIEWER_TOKEN": viewer_token,
        "CONFIGURATOR_ADMIN_TOKEN": admin_token,
        "DATABASE_URL": existing.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        "KNOWLEDGE_SCHEMA": existing.get("KNOWLEDGE_SCHEMA") or "knowledge",
        "OPS_SCHEMA": existing.get("OPS_SCHEMA") or "ops",
        "WARM_LOCAL_MODELS": existing.get("WARM_LOCAL_MODELS") or "false",
        "CONFIGURATOR_PREFILL_API_KEY": "true" if demo else existing.get("CONFIGURATOR_PREFILL_API_KEY", "false"),
        "CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES": existing.get("CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES") or "26214400",
        "CORS_ALLOW_ORIGINS": existing.get("CORS_ALLOW_ORIGINS") or "http://127.0.0.1:8000,http://localhost:8000",
    }


def ensure_env(
    provider: str | None = None,
    provider_key: str | None = None,
    demo: bool = True,
    path: Path = ENV_PATH,
    interactive: bool = True,
) -> dict[str, str]:
    existing = read_env(path)
    provider = (provider or existing.get("ACTIVE_PROVIDER") or "openai").strip().lower()
    key_name = provider_key_name(provider)
    provider_key = provider_key or existing.get(key_name) or ""
    if not provider_key and interactive:
        print(f"{key_name} required. Token is written only to local .env.")
        provider_key = getpass.getpass(f"{key_name}: ").strip()
    if not provider_key:
        raise SystemExit(f"{key_name} is required. Pass --provider-key or run interactively.")
    updates = build_env_updates(existing, provider, provider_key, demo)
    write_env(updates, path)
    return {**existing, **updates}


def product_config(product_name: str) -> dict:
    slug = _slugify(product_name)
    return {
        "products": {
            slug: {
                "display_name": product_name.strip() or "Example Product",
                "slug": slug,
                "aliases": [slug.replace("_", " "), "demo"],
                "default_product": True,
                "platforms": {
                    "website": {"normalized": "website", "aliases": ["web", "browser", "site"], "enabled": True},
                    "mobile_app": {"normalized": "app", "aliases": ["mobile", "app", "ios", "android"], "enabled": True},
                },
                "roles": {
                    "required": False,
                    "values": [
                        {"name": "admin", "aliases": ["administrator"]},
                        {"name": "agent", "aliases": ["support agent", "user"]},
                        {"name": "manager", "aliases": ["supervisor", "lead"]},
                    ],
                },
            }
        }
    }


def source_config(source_folder: str) -> dict:
    folder = source_folder.strip().rstrip("/") or "knowledge_loader/processed"
    return {
        "sources": {
            "custom_knowledge_base": {
                "enabled": False,
                "source_type": "official_help_article",
                "path": f"{folder}/knowledge_base.csv",
                "audience": "customer_facing",
                "required_columns": ["title", "content"],
                "column_mapping": {"title": "title", "content": "content", "url": "url", "url_name": "url_name"},
                "default_authority": 1.0,
            }
        }
    }


def write_json_yaml(path: Path, payload: dict, force: bool = False) -> bool:
    if path.exists() and not force:
        return False
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return True


def write_project_files(
    product_name: str,
    source_folder: str,
    root: Path = ROOT,
    force: bool = False,
) -> list[str]:
    written = []
    config_dir = root / "config"
    config_dir.mkdir(exist_ok=True)
    if write_json_yaml(config_dir / "products.yaml", product_config(product_name), force=force):
        written.append("config/products.yaml")
    if write_json_yaml(config_dir / "sources.yaml", source_config(source_folder), force=force):
        written.append("config/sources.yaml")
    sample_dir = root / "demo_data" / "onboarding"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_path = sample_dir / "sample_questions.txt"
    if force or not sample_path.exists():
        sample_path.write_text(
            DEFAULT_DEMO_TICKET + "\n"
            "Customer can access the website but mobile sign-in fails after an admin changed their role.\n",
            encoding="utf-8",
        )
        written.append("demo_data/onboarding/sample_questions.txt")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize ResolveKit local onboarding config.")
    parser.add_argument("--demo", action="store_true", help="Use demo-friendly local settings.")
    parser.add_argument("--provider", choices=["openai", "gemini"], help="Hosted LLM provider.")
    parser.add_argument("--provider-key", help="Provider API key. Prefer interactive prompt for local use.")
    parser.add_argument("--product-name", default="Example Product")
    parser.add_argument("--source-folder", default="knowledge_loader/processed")
    parser.add_argument("--force", action="store_true", help="Overwrite config/products.yaml and config/sources.yaml.")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt for missing provider key.")
    args = parser.parse_args()

    env = ensure_env(
        provider=args.provider,
        provider_key=args.provider_key,
        demo=args.demo,
        interactive=not args.non_interactive,
    )
    written = write_project_files(args.product_name, args.source_folder, force=args.force)

    print("ResolveKit onboarding config ready")
    print(f"Provider: {env['ACTIVE_PROVIDER']}")
    print(f"Env: {ENV_PATH.relative_to(ROOT)}")
    print(f"Viewer token: {env['API_KEY']}")
    print(f"Admin token: {env['CONFIGURATOR_API_KEY']}")
    if written:
        print("Written:")
        for item in written:
            print(f"  - {item}")
    else:
        print("Config files already existed; use --force to overwrite products/sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
