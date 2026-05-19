# Thin alias over the `./sandbox` CLI for muscle-memory users.
# The real interface is the CLI. Run `./sandbox` for help.

.DEFAULT_GOAL := help

%:
	@./sandbox $(MAKECMDGOALS)

help:
	@./sandbox

.PHONY: help
