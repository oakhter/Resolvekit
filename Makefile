.PHONY: get-started demo doctor init-project setup-db reset-demo reload-kb

get-started:
	./get_started.sh

demo:
	./get_started.sh

doctor:
	./scripts/demo_doctor.sh

init-project:
	docker compose exec onboarding python scripts/init_project.py

setup-db:
	.venv/bin/python scripts/setup_db.py

reset-demo:
	.venv/bin/python scripts/rebuild_db.py
	.venv/bin/python knowledge_loader/kb_loader.py --all

reload-kb:
	.venv/bin/python knowledge_loader/kb_loader.py --all
