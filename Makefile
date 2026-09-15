VENV := venv
OMBOOTSTRAP := $(VENV)/bin/ombs
PYTHON := python3
INSTALL_BINARY_NAME := ombs
INSTALL_TARGET := ${HOME}/.local/bin/$(INSTALL_BINARY_NAME)

.PHONY: OMBS_HELP install venv_dev
DEFAULT: OMBS_HELP

$(VENV)/bin/pip3:
	$(PYTHON) -m venv $(VENV)

venv_dev: $(VENV)/bin/pip3 pyproject.toml requirements.txt requirements-dev.txt
	$(VENV)/bin/pip3 install -r requirements-dev.txt

$(OMBOOTSTRAP): $(VENV)/bin/pip3 pyproject.toml requirements.txt
	$(VENV)/bin/pip3 install -r requirements.txt

OMBS_HELP: $(OMBOOTSTRAP)
	$(OMBOOTSTRAP) --help

install: $(INSTALL_TARGET)

$(INSTALL_TARGET): $(OMBOOTSTRAP)
	mkdir -p "$$(dirname "$(INSTALL_TARGET)")"
	ln -srvf "$(OMBOOTSTRAP)" "$(INSTALL_TARGET)"
