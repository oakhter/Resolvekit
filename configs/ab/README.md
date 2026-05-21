# ResolveKit A/B Configs

Generated from `docs/resolvekit_ab_testing_plan_v6.md` by:

```bash
.venv/bin/python scripts/materialize_ab_configs.py
```

Rules:

- Offline replay only during alpha.
- One changed lever per variant.
- Five variants plus control per stage.
- Negative results stay in `experiments/reports/` or `experiments/decisions/`.
- No config here changes the runtime default by itself.
