# ResolveKit A/B Configs

Advanced experiments only. Ignore `configs/ab/` unless you are running offline retrieval, drafting, citation, validation, or confidence experiments.

These configs are **not needed for the first demo**.

Use them only when improving retrieval, drafting, citation, validation, or confidence metrics after the basic Docker demo works.

Regenerate these variants, if needed, with:

```bash
.venv/bin/python scripts/materialize_ab_configs.py
```

Rules:

- Offline replay only during the developer preview.
- One changed lever per variant.
- Five variants plus control per stage.
- Negative results stay in `experiments/reports/` or `experiments/decisions/`.
- No config here changes the runtime default by itself.
