# allow: SIZE_OK — single-module layout is pinned by the v3 plan (Task 9 CLI
# import + Task 17 onboarding import these exact names); ~85 of the measured
# lines are upstream-verbatim map data tables and the contract docstring.
"""Legacy ``oh-my-openagent*.json`` / ``oh-my-opencode*.json`` → v3 transform.

Pure functions only — the sole I/O-adjacent members are ``discover_legacy``
and ``derive_profile_name`` (path math, no reads).  ``transform_legacy``
returns ``(document, warnings)`` where ``document`` is always shaped
``{"$schema": OMO_SCHEMA_URL, "[opencode]": {...}}``; it never emits
``_migrations``.

Pipeline order (binding for Task 9 CLI import / Task 17 onboarding):

1. :func:`strip_metadata` — drop ``$schema``/``_migrations``/``appliedMigrations``.
2. :func:`rename_agents` — rename agent ROUTE KEYS via ``AGENT_NAME_MAP``.
3. :func:`bump_model_versions` — route-level string ``model`` only, applied
   to both ``agents`` and ``categories`` (fallback list entries are never
   bumped; mirrors upstream ``migrateModelVersions``).
4. :func:`rename_keys` — ``omo_agent``→``sisyphus_agent`` (when both exist
   the ``omo_agent`` value wins), drop ``lsp``, promote
   ``experimental.hashline_edit`` to top level when absent.
5. :func:`remap_disabled` — ``disabled_agents`` via ``AGENT_NAME_MAP``,
   ``disabled_hooks`` via ``HOOK_NAME_MAP`` (``None`` drops the entry; a
   fully-dropped list stays as an empty list).
6. ``_restructure_categories`` — ``{model, variant, fallback_models}`` →
   ``{models: [primary, *fallbacks]}``; the primary is a bare string when it
   carries no reasoning-ish settings, else ``{model, <settings>}``.
7. ``_restructure_agents`` — canonicalize agent ``fallback_models`` chains
   using task-1 helpers (applies only to agents with ``fallback_models``;
   model-only and model+models agents pass through untouched).
8. :func:`normalize_model_entry` on every dict model-ref (agents: the route
   dict — chain entries were already normalized in step 7; categories:
   every dict ``models`` entry).

Observed-pair asymmetry (the real migration output, which this transform
reproduces byte-for-byte modulo ID sanitization): an AGENT fallback dict
that normalizes down to ``{"model": ...}`` alone collapses to the bare model
string, while CATEGORY ``models`` entries never collapse.  Rename collisions
in ``rename_agents`` overwrite: the later route in iteration order wins.
Dict entries without a ``model`` key are left verbatim.
"""

from __future__ import annotations

from pathlib import Path

from opencode_config_switcher.omoconfig import OMO_SCHEMA_URL
from opencode_config_switcher.paths import Paths

__all__ = [
    "AGENT_NAME_MAP",
    "HOOK_NAME_MAP",
    "MODEL_VERSION_MAP",
    "bump_model_versions",
    "canonicalize_definition",
    "definition_needs_migration",
    "derive_profile_name",
    "discover_legacy",
    "migrate_document",
    "normalize_model_entry",
    "remap_disabled",
    "rename_agents",
    "rename_keys",
    "strip_metadata",
    "transform_legacy",
]

# Upstream oh-my-openagent dist/index.js:10097-10129 (agent-names.ts).
AGENT_NAME_MAP = {
    "omo": "sisyphus",
    "OmO": "sisyphus",
    "Sisyphus": "sisyphus",
    "Sisyphus (Ultraworker)": "sisyphus",
    "sisyphus": "sisyphus",
    "Hephaestus (Deep Agent)": "hephaestus",
    "OmO-Plan": "prometheus",
    "omo-plan": "prometheus",
    "Planner-Sisyphus": "prometheus",
    "planner-sisyphus": "prometheus",
    "Prometheus - Plan Builder": "prometheus",
    "Prometheus (Plan Builder)": "prometheus",
    "prometheus": "prometheus",
    "orchestrator-sisyphus": "atlas",
    "Atlas": "atlas",
    "Atlas (Plan Executor)": "atlas",
    "atlas": "atlas",
    "plan-consultant": "metis",
    "Metis - Plan Consultant": "metis",
    "Metis (Plan Consultant)": "metis",
    "metis": "metis",
    "Momus - Plan Critic": "momus",
    "Momus (Plan Critic)": "momus",
    "momus": "momus",
    "Sisyphus-Junior": "sisyphus-junior",
    "sisyphus-junior": "sisyphus-junior",
    "build": "build",
    "oracle": "oracle",
    "librarian": "librarian",
    "explore": "explore",
    "multimodal-looker": "multimodal-looker",
}

# Upstream hook-names.ts (dist/index.js:10131-10141).
HOOK_NAME_MAP = {
    "anthropic-auto-compact": "anthropic-context-window-limit-recovery",
    "sisyphus-orchestrator": "atlas",
    "sisyphus-gpt-hephaestus-reminder": "no-sisyphus-gpt",
    "empty-message-sanitizer": None,
    "delegate-task-english-directive": None,
    "gpt-permission-continuation": None,
    "thinking-block-validator": None,
    "session-recovery": None,
}

# Upstream model-versions.ts: legacy route primaries that must be bumped.
MODEL_VERSION_MAP = {
    "anthropic/claude-opus-4-4": "anthropic/claude-opus-4-8",
}

# Reasoning-unification keys in precedence order (first present wins).
_REASONING_KEYS = ("reasoning", "reasoningEffort", "variant")
# Keys the normalized entry never keeps (folded or renamed away).
_DROP_KEYS = (
    "variant", "reasoningEffort", "thinking", "textVerbosity",
    "maxTokens", "providerOptions",
)
_METADATA_KEYS = ("$schema", "_migrations", "appliedMigrations")
# Definition-level settings keys folded into the primary chain entry, then
# stripped from the definition (OMO primaryModelRef / normalizeDefinition,
# dist/cli-node/index.js:87418-87435 and 87478-87489).
_DEFINITION_SETTINGS_KEYS = (
    "reasoning", "variant", "reasoningEffort", "thinking",
    "textVerbosity", "provider_options", "providerOptions",
)
_DEFINITION_STRIP_KEYS = ("model", "fallback_models",
                          *_DEFINITION_SETTINGS_KEYS)


def strip_metadata(raw: dict) -> dict:
    """Return a copy of ``raw`` without top-level migration metadata."""
    return {k: v for k, v in raw.items() if k not in _METADATA_KEYS}


def rename_agents(agents: dict) -> dict:
    """Rename agent route keys via ``AGENT_NAME_MAP`` (exact, then lower).

    Values are untouched.  When two keys rename to the same target the
    LATER route in iteration order overwrites the earlier one.
    """
    migrated: dict = {}
    for key, value in agents.items():
        new_key = (AGENT_NAME_MAP.get(key)
                   or AGENT_NAME_MAP.get(key.lower())
                   or key)
        migrated[new_key] = value
    return migrated


def bump_model_versions(routes: dict) -> dict:
    """Bump the route-level string ``model`` of each entry in ``routes``.

    Fallback/model-list entries are deliberately NOT bumped (upstream
    ``migrateModelVersions`` only inspects ``config.model``).
    """
    bumped: dict = {}
    for name, route in routes.items():
        if (isinstance(route, dict)
                and isinstance(route.get("model"), str)
                and route["model"] in MODEL_VERSION_MAP):
            bumped[name] = {
                **route, "model": MODEL_VERSION_MAP[route["model"]]}
        else:
            bumped[name] = route
    return bumped


def rename_keys(config: dict) -> dict:
    """Apply the legacy-key renames: ``omo_agent``, ``lsp``, ``experimental``."""
    renamed = dict(config)
    if "omo_agent" in renamed:
        renamed["sisyphus_agent"] = renamed.pop("omo_agent")
    renamed.pop("lsp", None)
    experimental = renamed.get("experimental")
    if isinstance(experimental, dict) and "hashline_edit" in experimental:
        if "hashline_edit" not in renamed:
            renamed["hashline_edit"] = experimental["hashline_edit"]
        remainder = {k: v for k, v in experimental.items()
                     if k != "hashline_edit"}
        if remainder:
            renamed["experimental"] = remainder
        else:
            renamed.pop("experimental", None)
    return renamed


def remap_disabled(config: dict) -> dict:
    """Rename ``disabled_agents``/``disabled_hooks`` list entries in place.

    Hooks mapped to ``None`` are dropped; a list whose every entry drops
    stays as an empty list (the key is kept).  Non-list values and absent
    keys are left untouched.
    """
    remapped = dict(config)
    disabled_agents = remapped.get("disabled_agents")
    if isinstance(disabled_agents, list):
        remapped["disabled_agents"] = [
            (AGENT_NAME_MAP.get(entry)
             or AGENT_NAME_MAP.get(entry.lower())
             or entry) if isinstance(entry, str) else entry
            for entry in disabled_agents
        ]
    disabled_hooks = remapped.get("disabled_hooks")
    if isinstance(disabled_hooks, list):
        kept = []
        for entry in disabled_hooks:
            if not isinstance(entry, str):
                kept.append(entry)
            elif entry in HOOK_NAME_MAP:
                replacement = HOOK_NAME_MAP[entry]
                if replacement is not None:
                    kept.append(replacement)
            else:
                kept.append(entry)
        remapped["disabled_hooks"] = kept
    return remapped


def normalize_model_entry(entry: dict,
                          path: tuple[str, ...]) -> tuple[dict, tuple[str, ...]]:
    """Apply the reasoning-unification rules to ONE model-ref dict.

    Precedence ``reasoning`` > ``reasoningEffort`` > ``variant``; when two
    or more are present the survivors-after-first are dropped with a
    ``conflict: {path} dropped {k}={v!r} ... kept {key}={value!r}``
    warning.  ``thinking`` with ``type: "disabled"`` yields
    ``reasoning: "off"`` only when no explicit reasoning source exists;
    with ``type: "enabled"`` it folds into ``provider_options.thinking``.
    ``textVerbosity`` folds into ``provider_options.textVerbosity``;
    ``providerOptions`` merges into ``provider_options`` (camel wins on
    collision); ``maxTokens`` becomes ``max_tokens`` only when ``max_tokens``
    is absent.  Dropped keys: ``variant``, ``reasoningEffort``, ``thinking``,
    ``textVerbosity``, ``maxTokens``, ``providerOptions``.  No fields are
    invented; unknown keys pass through.
    """
    present = [key for key in _REASONING_KEYS if key in entry]
    warnings: list[str] = []
    kept_key = present[0] if present else None
    reasoning_value = entry[kept_key] if kept_key is not None else None
    if len(present) >= 2:
        dropped = " ".join(f"{key}={entry[key]!r}" for key in present[1:])
        warnings.append(
            f"conflict: {'.'.join(path)} dropped {dropped} "
            f"kept {kept_key}={reasoning_value!r}")

    thinking = entry.get("thinking")
    thinking = thinking if isinstance(thinking, dict) else None
    if reasoning_value is None and thinking is not None \
            and thinking.get("type") == "disabled":
        reasoning_value = "off"

    provider_options: dict = {}
    snake = entry.get("provider_options")
    if isinstance(snake, dict):
        provider_options.update(snake)
    camel = entry.get("providerOptions")
    if isinstance(camel, dict):
        provider_options.update(camel)
    if thinking is not None and thinking.get("type") == "enabled":
        provider_options["thinking"] = dict(thinking)
    if "textVerbosity" in entry:
        provider_options["textVerbosity"] = entry["textVerbosity"]

    normalized: dict = {}
    for key, value in entry.items():
        if key == kept_key and key != "reasoning":
            normalized["reasoning"] = reasoning_value
        elif key in _DROP_KEYS:
            continue
        else:
            normalized[key] = value
    if reasoning_value is not None and "reasoning" not in normalized:
        normalized["reasoning"] = reasoning_value
    if provider_options:
        normalized["provider_options"] = provider_options
    if "max_tokens" not in normalized and "maxTokens" in entry:
        normalized["max_tokens"] = entry["maxTokens"]
    return normalized, tuple(warnings)


def _as_chain_list(value) -> list:
    """OMO ``normalizedList`` shape: wrap non-list values, drop ``None``."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _canonical_chain_entry(entry):
    """Normalize one chain entry; collapse ``{"model": ...}``-only dicts."""
    if isinstance(entry, dict) and "model" in entry:
        normalized, _warnings = normalize_model_entry(entry, ("models",))
        return (normalized["model"]
                if set(normalized) == {"model"} else normalized)
    return entry


def canonicalize_definition(block: dict, kind: str) -> dict:
    """Fold ``model``(+settings) + ``fallback_models`` into canonical ``models``.

    OMO parity (``normalizeDefinition`` dist/cli-node/index.js:87447-87491,
    ``primaryModelRef`` 87418-87435): the chain is
    ``[primary?, *existing, *fallbacks]`` where ``primary`` is the string
    ``model`` folded with the definition-level settings keys
    (``_DEFINITION_SETTINGS_KEYS``), ``existing`` is the prior ``models``
    list (``kind == "agent"`` only — category ``models`` are replaced, never
    carried over; OMO 87475), and every dict entry passes through
    :func:`normalize_model_entry`.  Entries that normalize down to
    ``{"model": ...}`` alone collapse to the bare model string (tool
    convention, editor ``_collapse_agent_entry``).  ``model``,
    ``fallback_models``, and all settings keys are deleted from the
    definition level; every other key (``description``, ``tools``,
    ``_comment`` …) is preserved verbatim.  Pure: blocks without
    ``fallback_models`` are returned as an unchanged shallow copy and the
    input is never mutated.
    """
    if "fallback_models" not in block:
        return dict(block)
    result = {key: value for key, value in block.items()
              if key not in _DEFINITION_STRIP_KEYS}
    chain: list = []
    model = block.get("model")
    if isinstance(model, str):
        settings = {key: block[key]
                    for key in _DEFINITION_SETTINGS_KEYS if key in block}
        chain.append({"model": model, **settings} if settings else model)
    if kind == "agent":
        chain.extend(_as_chain_list(block.get("models")))
    chain.extend(_as_chain_list(block.get("fallback_models")))
    result["models"] = [_canonical_chain_entry(entry) for entry in chain]
    return result


def definition_needs_migration(block) -> bool:
    """True iff ``block`` is a dict carrying the legacy ``fallback_models``."""
    return isinstance(block, dict) and "fallback_models" in block


def migrate_document(document: dict) -> tuple[dict, int]:
    """Canonicalize every agent/category route under harness-shaped keys.

    Harness-shaped keys match ``[...]`` (e.g. ``"[opencode]"``); within each
    harness block the ``agents`` and ``categories`` dicts are rewritten via
    :func:`canonicalize_definition`.  Returns ``(new_document, converted)``
    where ``converted`` counts the routes that carried ``fallback_models``.
    Non-harness keys and non-route keys (``model_fallback``,
    ``runtime_fallback``, the catalog ``models`` mapping, ``_migrations`` …)
    pass through untouched.  Pure: the input document is never mutated.
    """
    migrated = dict(document)
    converted = 0
    for key, value in document.items():
        if not (key.startswith("[") and key.endswith("]")):
            continue
        if not isinstance(value, dict):
            continue
        block = dict(value)
        for section, kind in (("agents", "agent"),
                              ("categories", "category")):
            routes = value.get(section)
            if not isinstance(routes, dict):
                continue
            rebuilt: dict = {}
            for name, route in routes.items():
                if definition_needs_migration(route):
                    rebuilt[name] = canonicalize_definition(route, kind)
                    converted += 1
                else:
                    rebuilt[name] = route
            block[section] = rebuilt
        migrated[key] = block
    return migrated, converted


def _restructure_agents(config: dict) -> dict:
    """Canonicalize agent ``fallback_models`` chains using task-1 helpers."""
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return config
    rebuilt: dict = {}
    for name, agent in agents.items():
        if not isinstance(agent, dict):
            rebuilt[name] = agent
            continue
        # Only canonicalize agents that have fallback_models (task-1 semantics)
        if not definition_needs_migration(agent):
            rebuilt[name] = agent
            continue
        rebuilt[name] = canonicalize_definition(agent, "agent")
    return {**config, "agents": rebuilt}


def _restructure_categories(config: dict) -> dict:
    """Fold category ``model``(+settings) + ``fallback_models`` into ``models``."""
    categories = config.get("categories")
    if not isinstance(categories, dict):
        return config
    rebuilt: dict = {}
    for name, category in categories.items():
        if (not isinstance(category, dict) or "models" in category
                or ("model" not in category
                    and "fallback_models" not in category)):
            rebuilt[name] = category
            continue
        models: list = []
        model = category.get("model")
        if isinstance(model, str):
            settings = {key: category[key]
                        for key in (*_REASONING_KEYS, "thinking")
                        if key in category}
            models.append({"model": model, **settings} if settings else model)
        fallbacks = category.get("fallback_models")
        if isinstance(fallbacks, list):
            models.extend(fallbacks)
        elif isinstance(fallbacks, str):
            models.append(fallbacks)
        remainder = {key: value for key, value in category.items()
                     if key not in ("model", "fallback_models",
                                    *_REASONING_KEYS, "thinking")}
        rebuilt[name] = {**remainder, "models": models}
    return {**config, "categories": rebuilt}


def _normalize_agent_fallbacks(fallbacks: list, route_path: tuple[str, ...],
                               collected: list[str]) -> list:
    rebuilt = []
    for index, entry in enumerate(fallbacks):
        if isinstance(entry, dict) and "model" in entry:
            normalized, warns = normalize_model_entry(
                entry, (*route_path, "fallback_models", str(index)))
            collected.extend(warns)
            # observed-pair rule: an agent fallback that normalizes down to
            # {"model": ...} alone collapses to the bare model string
            rebuilt.append(
                normalized["model"]
                if set(normalized) == {"model"} else normalized)
        else:
            rebuilt.append(entry)
    return rebuilt


def _normalize_category_models(models: list, route_path: tuple[str, ...],
                               collected: list[str]) -> list:
    rebuilt = []
    for index, entry in enumerate(models):
        if isinstance(entry, dict) and "model" in entry:
            normalized, warns = normalize_model_entry(
                entry, (*route_path, "models", str(index)))
            collected.extend(warns)
            rebuilt.append(normalized)  # category entries never collapse
        else:
            rebuilt.append(entry)
    return rebuilt


def _normalize_entries(config: dict) -> tuple[dict, tuple[str, ...]]:
    collected: list[str] = []
    result = dict(config)
    agents = result.get("agents")
    if isinstance(agents, dict):
        rebuilt_agents: dict = {}
        for name, route in agents.items():
            if not isinstance(route, dict):
                rebuilt_agents[name] = route
                continue
            normalized, warns = normalize_model_entry(
                route, ("agents", name))
            collected.extend(warns)
            fallbacks = normalized.get("fallback_models")
            if isinstance(fallbacks, list):
                normalized["fallback_models"] = _normalize_agent_fallbacks(
                    fallbacks, ("agents", name), collected)
            rebuilt_agents[name] = normalized
        result["agents"] = rebuilt_agents
    categories = result.get("categories")
    if isinstance(categories, dict):
        rebuilt_categories: dict = {}
        for name, category in categories.items():
            if not isinstance(category, dict):
                rebuilt_categories[name] = category
                continue
            models = category.get("models")
            if isinstance(models, list):
                category = {**category, "models": _normalize_category_models(
                    models, ("categories", name), collected)}
            rebuilt_categories[name] = category
        result["categories"] = rebuilt_categories
    return result, tuple(collected)


def transform_legacy(raw: dict) -> tuple[dict, tuple[str, ...]]:
    """Transform one legacy document into ``{"$schema", "[opencode]"}``.

    Non-dict ``raw`` yields ``({}, ("legacy document is not an object",))``.
    Pipeline: steps 1-5, then ``_restructure_categories``, then
    ``_restructure_agents`` (agent ``fallback_models`` routes fold into
    canonical ``models`` chains).  The returned document never contains
    ``_migrations`` and never carries ``fallback_models`` under any
    ``agents`` block.
    """
    if not isinstance(raw, dict):
        return {}, ("legacy document is not an object",)
    config = strip_metadata(raw)
    if isinstance(config.get("agents"), dict):
        config["agents"] = bump_model_versions(
            rename_agents(config["agents"]))
    if isinstance(config.get("categories"), dict):
        config["categories"] = bump_model_versions(config["categories"])
    config = rename_keys(config)
    config = remap_disabled(config)
    config = _restructure_categories(config)
    config = _restructure_agents(config)
    body, warnings = _normalize_entries(config)
    return {"$schema": OMO_SCHEMA_URL, "[opencode]": body}, warnings


def discover_legacy(paths: Paths) -> list[Path]:
    """Glob legacy configs in ``paths.legacy_dir`` (both naming prefixes).

    ``.BAK``-suffixed names are excluded; results are deduped and sorted.
    """
    if not paths.legacy_dir.is_dir():
        return []
    found: list[Path] = []
    seen: set[str] = set()
    for pattern in ("oh-my-openagent*.json", "oh-my-opencode*.json"):
        for path in paths.legacy_dir.glob(pattern):
            if path.name in seen or path.name.endswith(".BAK"):
                continue
            found.append(path)
            seen.add(path.name)
    return sorted(found)


def derive_profile_name(path: Path) -> str:
    """Profile name for a legacy config: stem minus the naming prefix.

    Bare canonical stems (``oh-my-openagent`` / ``oh-my-opencode``) map to
    ``"default"``; unprefixed stems pass through unchanged.
    """
    stem = path.stem
    if stem in ("oh-my-openagent", "oh-my-opencode"):
        return "default"
    for prefix in ("oh-my-openagent-", "oh-my-opencode-"):
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem
