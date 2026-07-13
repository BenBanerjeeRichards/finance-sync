build:
	docker build . -t ghcr.io/benbanerjeerichards/finance-sync
	docker push ghcr.io/benbanerjeerichards/finance-sync

make-migrations message:
    PSQL_CONNECTION_STRING=postgresql://myuser:mypassword@localhost:5432/mydb alembic revision --autogenerate -m "{{message}}"

migrate:
    PSQL_CONNECTION_STRING=postgresql://myuser:mypassword@localhost:5432/mydb alembic upgrade head