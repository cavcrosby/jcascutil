include base.mk

# recursive variables

# include other generic makefiles
include python.mk
# overrides defaults set by included makefiles
VIRTUALENV_PYTHON_VERSION = 3.9.5
PYTHON_VIRTUALENV_NAME = $(shell basename ${CURDIR})

# executables
JCASCUTIL = jcascutil.py

# simply expanded variables
executables := \
	${python_executables}

_check_executables := $(foreach exec,${executables},$(if $(shell command -v ${exec}),pass,$(error "No ${exec} in PATH")))

.PHONY: ${HELP}
${HELP}:
	# inspired by the makefiles of the Linux kernel and Mercurial
>	@echo 'Available make targets:'
>	@echo '  ${SETUP}              - creates and configures the virtualenv to be used'
>	@echo '                       by the project, also useful for development'
>	@echo '  ${INSTALL}            - makes the program available to use from the filesystem'
>	@echo '  ${UNINSTALL}          - removes the program from the filesystem and uninstalls'
>	@echo '                       the virtualenv'

.PHONY: ${SETUP}
${SETUP}: ${PYENV_POETRY_SETUP}

# .ONESHELL is needed to ensure all the commands below run in one shell session.
# A makefile 'define' variable does not work because we will want make to
# evaluate some of the vars before being passed off to the shell.
.ONESHELL:
.PHONY: ${INSTALL}
${INSTALL}:
>	cat << '_EOF_' > "${bin_dir}/${JCASCUTIL}"
>	#!/bin/bash
>	#
>	# Small shim that calls the program below in the proper python virtualenv.
>	# PYENV_VERSION allows the program to run in the PYTHON_VIRTUALENV_NAME without doing
>	# additional shell setup. pyenv will still process the program name through an
>	# appropriately (same) named shim but this will ultimately still call this shim.
>	export PYENV_VERSION="${PYTHON_VIRTUALENV_NAME}"
>	"${CURDIR}/${JCASCUTIL}" "$$@"
>	unset PYENV_VERSION
>	
>	_EOF_
>
>	chmod 755 "${bin_dir}/${JCASCUTIL}"

.PHONY: ${UNINSTALL}
${UNINSTALL}:
>	rm --force "${bin_dir}/${JCASCUTIL}"
>	${PYENV} uninstall --force "${PYTHON_VIRTUALENV_NAME}"
