# special makefile variables
.DEFAULT_GOAL := help
.RECIPEPREFIX := >

# recursive variables
SHELL = /usr/bin/sh

# executables
POETRY = poetry
PIP = pip
PYENV = pyenv
PYTHON = python
JCASCUTIL = jcascutil
executables = \
	${POETRY}\
	${PIP}\
	${PYENV}\
	${PYTHON}

# gnu install directory variables
prefix = ${HOME}/.local
exec_prefix = ${prefix}
# where to add link names that point to repo scripts
bin_dir = ${exec_prefix}/bin

# targets
HELP = help
SETUP = setup
INSTALL = install
UNINSTALL = uninstall
CLEAN = clean

# to be passed in at make runtime
VIRTUALENV_PYTHON_VERSION =

# simply expanded variables
# f ==> file
entry_point := ${CURDIR}/${JCASCUTIL}.py
virtenv_name := $(shell basename ${CURDIR})

# inspired from:
# https://stackoverflow.com/questions/5618615/check-if-a-program-exists-from-a-makefile#answer-25668869
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
>	@echo 'Public make configurations (e.g. make [config]=1 [targets]):'
>	@echo '  bin_dir                       - determines where the program is installed/uninstalled'
>	@echo '                                  from (default is "${bin_dir}")'
>	@echo '  VIRTUALENV_PYTHON_VERSION     - python version used by the project virtualenv (e.g. 3.8.2)'

.PHONY: ${SETUP}
${SETUP}:
>	@[ -n "${VIRTUALENV_PYTHON_VERSION}" ] || { echo "VIRTUALENV_PYTHON_VERSION was not passed into make"; exit 1; }
	# assumes that the VIRTUALENV_PYTHON_VERSION is already installed by pyenv
>	${PYENV} virtualenv "${VIRTUALENV_PYTHON_VERSION}" "${virtenv_name}"
	# mainly used to enter the virtualenv when in the repo
>	${PYENV} local "${virtenv_name}"
>	export PYENV_VERSION="${virtenv_name}"
	# to ensure the most current versions of dependencies can be installed
>	${PYTHON} -m ${PIP} install --upgrade ${PIP}
>	${PYTHON} -m ${PIP} install ${POETRY}
	# --no-root because we only want to install dependencies. 'pyenv exec' is needed
	# as poetry is installed into a virtualenv bin dir that is not added to the
	# current shell PATH.
>	${PYENV} exec ${POETRY} install --no-root || { echo "${POETRY} failed to install project dependencies"; exit 1; }
>	unset PYENV_VERSION

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
>	# PYENV_VERSION allows the program to run in the virtenv_name without doing
>	# additional shell setup. pyenv will still process the program name through an
>	# appropriately (same) named shim but this will ultimately still call this shim.
>	export PYENV_VERSION="${virtenv_name}"
>	"${entry_point}" "$$@"
>	unset PYENV_VERSION
>	
>	_EOF_
>
>	chmod 755 "${bin_dir}/${JCASCUTIL}"

.PHONY: ${UNINSTALL}
${UNINSTALL}:
>	rm --force "${bin_dir}/${JCASCUTIL}"
>	${PYENV} uninstall --force "${virtenv_name}"
