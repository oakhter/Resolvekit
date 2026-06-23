# ResolveKit A/B Configs

Historical advanced experiments only. ResolveKit is frozen, so these configs are retained as examples and offline replay fixtures, not an active roadmap.

These configs are **not needed for the first demo**.

Use them only when studying or replaying retrieval, drafting, citation, validation, or confidence experiments.

Regenerate these variants, if needed, with:

```bash
.venv/bin/python scripts/materialize_ab_configs.py
```

Rules:

- Offline replay only.
- One changed lever per variant.
- Five variants plus control per stage.
- No config here changes the runtime default by itself.
