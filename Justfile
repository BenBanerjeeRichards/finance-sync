build:
	docker build . -t ghcr.io/benbanerjeerichards/finance-sync
	docker push ghcr.io/benbanerjeerichards/finance-sync

make-migrations message:
    PSQL_CONNECTION_STRING=postgresql://myuser:mypassword@localhost:5432/mydb alembic revision --autogenerate -m "{{message}}"

migrate:
    PSQL_CONNECTION_STRING=postgresql://myuser:mypassword@localhost:5432/mydb alembic upgrade head


deploy:
	#!/usr/bin/env bash
	set -euo pipefail
	values_file=/Users/bbr/dev/infra/server/values-prod.yaml

	docker build . -t ghcr.io/benbanerjeerichards/finance-sync
	push_output=$(docker push ghcr.io/benbanerjeerichards/finance-sync | tee /dev/stderr)

	digest=$(echo "$push_output" | grep -oE 'digest: sha256:[a-f0-9]{64}' | awk '{print $2}')
	if [ -z "$digest" ]; then
		echo "ERROR: could not extract digest from docker push output, aborting before touching $values_file" >&2
		exit 1
	fi

	old_line=$(grep '^financeSyncImage:' "$values_file")
	new_line="financeSyncImage: ghcr.io/benbanerjeerichards/finance-sync@${digest}"
	sed -i '' "s|^financeSyncImage:.*|${new_line}|" "$values_file"
	echo "Updated $values_file:"
	echo "  before: ${old_line}"
	echo "  after:  ${new_line}"

	cd /Users/bbr/dev/infra/server
	helm upgrade prod . -f values-prod.yaml
