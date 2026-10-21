# Architecture guidelines

## Defaults

New backend services are written in Go, and PostgreSQL is the default database.
Services talk to each other over gRPC internally and expose REST to partners.

## Decisions

Adding a new third-party dependency requires an architecture decision record.
The architecture board reviews decision records every two weeks.
