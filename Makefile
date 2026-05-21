.PHONY: get-started demo doctor init-project

get-started:
	./get_started.sh

demo:
	./get_started.sh

doctor:
	docker compose exec onboarding python scripts/onboarding_doctor.py

init-project:
	docker compose exec onboarding python scripts/init_project.py
