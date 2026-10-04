# Airspace stack

Offline generation needs no provider credential. From the repository root:

```powershell
Copy-Item infra/airspace/.env.example infra/airspace/.env
docker compose --env-file infra/airspace/.env -f infra/airspace/compose.yaml config --quiet
docker compose --env-file infra/airspace/.env -f infra/airspace/compose.yaml --profile app up -d --build
docker compose --env-file infra/airspace/.env -f infra/airspace/compose.yaml run --rm airspace-cli generate --config config/scenarios/airspace_demo.yaml --seed 42 --run-id demo-1
docker compose -p garuda-airspace-test --env-file infra/airspace/.env -f infra/airspace/compose.yaml -f infra/airspace/compose.test.yaml --profile test run --build --rm store-tests
```

`docker compose --profile app down` keeps airspace volumes. Reset only this domain with `docker compose -f infra/airspace/compose.yaml --profile app down -v`. Regenerate the fixture lock with `uv pip compile infra/airspace/tests/requirements.in --generate-hashes --output-file infra/airspace/tests/requirements.lock`; regenerate the seed snapshot only after reviewing intentional generator changes. Live OpenAIP is deliberately disabled until its authenticated API contract and redistribution terms are validated.
