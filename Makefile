# Thin alias over the `./sb` CLI for muscle-memory users.
# The real interface is the CLI. Run `./sb` for help.

.DEFAULT_GOAL := help

%:
	@./sb $(MAKECMDGOALS)

help:
	@./sb

.PHONY: help
