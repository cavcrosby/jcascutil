#!/usr/bin/env python3
"""A tool that works with configuration as code (CasC) files for Jenkins."""
# Standard Library Imports
import argparse
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import traceback

# Third Party Imports
import ruamel.yaml

# import inspired from:
# https://stackoverflow.com/questions/35433838/how-to-dump-a-folded-scalar-to-yaml-in-python-using-ruamel#answer-51980082
from ruamel.yaml import scalarstring
import toml

# Local Application Imports
from pylib.argparse import CustomRawDescriptionHelpFormatter

# general program configurations

PROGRAM_NAME = os.path.basename(os.path.abspath(__file__))
PROGRAM_ROOT = os.getcwd()


class JenkinsConfigurationAsCode:
    """A utility to aid in the construction of Jenkins images/containers.

    From a high level, the functionality implemented is: to allow Jenkins
    images to be created with differing jobs, allow Jenkins images to be
    created with different number of agents/nodes (these are optional
    and are mainly determine at runtime/container creation), the program
    can setup the PWD with resources needed to perform the two functions
    mentioned previously, and the program can perform a custom docker build.
    This is all in thanks to the job-dsl plugin, the JCasC plugin, and the
    ruamel.yaml library. Finally, Docker containers is the main the type
    of images constructed.

    Attributes
    ----------
    REPOS_TO_TRANSFER_DIR_NAME : str
        This directory is copied over to the docker container, for
        the job-dsl plugin to use.
    DEFAULT_STDOUT_FD : _io.TextIOWrapper
        Currently where any yaml output is directed to by default.
    YAML_PARSER_WIDTH : int
        Used by the yaml parser on when to start wrapping text.

    See Also
    --------
    job-dsl plugin ==> https://plugins.jenkins.io/job-dsl/
    JCasC plugin ==> https://www.jenkins.io/projects/jcasc/

    """

    # jenkins configurations as code (CasC) specifics

    JOB_DSL_ROOT_KEY_YAML = "jobs"
    JOB_DSL_SCRIPT_KEY_YAML = "script"
    JOB_DSL_FILENAME_REGEX = r".*job-dsl.*"
    CASC_FILENAME_REGEX = r"^.*casc.*\.ya?ml$"

    # jenkins key values related ({jenkins: {...}})

    JENKINS_ROOT_KEY_YAML = "jenkins"
    JENKINS_NODES_KEY_YAML = "nodes"
    PERMANENT_KEY_YAML = "permanent"
    LAUNCHER_KEY_YAML = "launcher"
    JNLP_KEY_YAML = "jnlp"
    WORKDIRSETTINGS_KEY_YAML = "workDirSettings"
    DISABLED_KEY_YAML = "disabled"
    FAIL_IF_WORKING_DIR_IS_MISSING_KEY_YAML = "failIfWorkDirIsMissing"
    INTERNELDIR_KEY_YAML = "internalDir"
    NAME_KEY_YAML = "name"
    NODE_DESCRIPTION_KEY_YAML = "nodeDescription"
    NUM_EXECUTORS_KEY_YAML = "numExecutors"
    REMOTEFS_KEY_YAML = "remoteFS"
    RENTENTION_STRATEGY_KEY_YAML = "retentionStrategy"

    NAME_ENV_VAR_NAME = "JENKINS_AGENT_NAME"
    NODE_DESCRIPTION_ENV_VAR_NAME = "JENKINS_AGENT_DESC"
    NUM_EXECUTORS_ENV_VAR_NAME = "JENKINS_AGENT_NUM_EXECUTORS"
    REMOTEFS_ENV_VAR_NAME = "JENKINS_AGENT_REMOTE_ROOT_DIR"

    # readFileFromWorkspace('./foo')
    READ_FILE_FROM_WORKSPACE_EXPRESSION_REGEX = (
        r"readFileFromWorkspace\(.+\)(?=\))"
    )
    # readFileFromWorkspace('./foo') ==> ./foo
    READ_FILE_FROM_WORKSPACE_ARGUMENT_REGEX = (
        r"(?<=readFileFromWorkspace\(').+(?='\))"
    )
    READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER = "__PLACEHOLDER__"
    READ_FILE_FROM_WORKSPACE_EXPRESSION_REPLACEMENT = (
        f"new File('{READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER}').text"
    )
    PWD_IDENTIFIER_REGEX = r"\.\/"

    # class specific misc

    REPOS_TO_TRANSFER_DIR_NAME = "projects"
    DEFAULT_STDOUT_FD = sys.stdout
    YAML_PARSER_WIDTH = 1000
    SHELL_VARIABLE_REGEX = r"\$\{{1}\w+\}{1}|\$[a-zA-Z_]\w*"
    # assumes that any parsing will be on vars that are known shell variables
    SHELL_VARIABLE_NAME_REGEX = r"(?<=\$\{)\w+(?=\})|(?<=\$)[a-zA-Z_]\w*"
    ENV_VAR_REGEX = r"^[a-zA-Z_]\w*=.+"

    # repo configurations

    DEFAULT_BASE_IMAGE_REPO_URL = (
        "https://github.com/cavcrosby/jenkins-docker-base"
    )
    DEFAULT_BASE_IMAGE_REPO_NAME = os.path.basename(
        DEFAULT_BASE_IMAGE_REPO_URL
    )
    GIT_CONFIG_FILE_PATH = "./jobs.toml"
    PROJECTS_DIR_PATH = f"{PROGRAM_ROOT}/{REPOS_TO_TRANSFER_DIR_NAME}"

    # subcommands labels

    SUBCOMMAND = "subcommand"
    ADDJOBS_SUBCOMMAND = "addjobs"
    ADDAGENT_PLACEHOLDER_SUBCOMMAND = "addagent-placeholder"
    ADDAGENT_PLACEHOLDER_SUBCOMMAND_CLI_NAME = (
        ADDAGENT_PLACEHOLDER_SUBCOMMAND.replace("_", "-")
    )
    SETUP_SUBCOMMAND = "setup"
    DOCKER_BUILD_SUBCOMMAND = "docker-build"
    DOCKER_BUILD_SUBCOMMAND_CLI_NAME = DOCKER_BUILD_SUBCOMMAND.replace(
        "_", "-"
    )

    # positional/optional argument labels
    # used at the command line and to reference values of arguments

    CASC_PATH_SHORT_OPTION = "c"
    CASC_PATH_LONG_OPTION = "casc_path"
    CASC_PATH_LONG_OPTION_CLI_NAME = CASC_PATH_LONG_OPTION.replace("_", "-")
    # as long as the short optional argument is not part
    # of the same subcommand, then it is ok
    CLEAN_SHORT_OPTION = "c"
    CLEAN_LONG_OPTION = "clean"
    ENV_VAR_SHORT_OPTION = "e"
    ENV_VAR_LONG_OPTION = "env"
    MERGE_CASC_SHORT_OPTION = "m"
    MERGE_CASC_LONG_OPTION = "merge_casc"
    MERGE_CASC_CLI_NAME = MERGE_CASC_LONG_OPTION.replace("_", "-")
    NUM_OF_AGENTS_TO_ADD_SHORT_OPTION = "n"
    NUM_OF_AGENTS_TO_ADD_LONG_OPTION = "numagents"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION = "t"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION = "transform_rffw"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME = (
        TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION.replace("_", "-")
    )
    DOCKER_TAG_POSITIONAL_ARG = "tag"
    DOCKER_OPT_SHORT_OPTION = "o"
    DOCKER_OPT_LONG_OPTION = "opt"
    RELEASE_BUILD_SHORT_OPTION = "r"
    RELEASE_BUILD_LONG_OPTION = "release"

    _DESC = """Description: A small utility to aid in the construction of Jenkins containers."""
    _arg_parser = argparse.ArgumentParser(
        description=_DESC,
        formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
            prog, max_help_position=35
        ),
        allow_abbrev=False,
    )
    _arg_subparsers = _arg_parser.add_subparsers(
        title=f"{SUBCOMMAND}s",
        dest=SUBCOMMAND,
        metavar=f"{SUBCOMMAND} [options ...]",
    )
    _arg_subparsers.required = True

    # following along to:
    # https://stackoverflow.com/questions/33645859/how-to-add-common-arguments-to-argparse-subcommands
    # this parser is not meant to be invoked with parse_args()!
    _common_parser = argparse.ArgumentParser(add_help=False)
    _common_parser.add_argument(
        f"-{CASC_PATH_SHORT_OPTION}",
        f"--{CASC_PATH_LONG_OPTION_CLI_NAME}",
        help="load custom casc instead from the default",
        metavar="CASC_PATH",
    )
    _common_parser.add_argument(
        f"-{ENV_VAR_SHORT_OPTION}",
        f"--{ENV_VAR_LONG_OPTION}",
        nargs="*",
        help="set environment variables, format: '<key>=<value>'",
    )
    _common_parser.add_argument(
        f"-{MERGE_CASC_SHORT_OPTION}",
        f"--{MERGE_CASC_CLI_NAME}",
        help="merge another casc file into the loaded casc",
        metavar="CASC_PATH",
    )

    def __init__(self):

        self.repo_commit = None
        self.repo_branch = None
        self.repo_names = list()
        self.casc = ruamel.yaml.comments.CommentedMap()
        self.toml = None
        self._yaml_parser = ruamel.yaml.YAML()
        self._yaml_parser.width = self.YAML_PARSER_WIDTH

    @staticmethod
    def _meets_job_dsl_filereqs(repo_name, job_dsl_files):
        """Check if the found job-dsl files meet specific requirements.

        Should note this is solely program specific and not
        related to the limitations/restrictions of the job-dsl plugin itself.

        Returns
        -------
        bool
            If all the job-dsl file(s) meet the program requirements.

        """
        num_of_job_dsls = len(job_dsl_files)
        if num_of_job_dsls == 0:
            print(
                f"{PROGRAM_NAME}: {repo_name} does not have a job-dsl file, "
                "skip",
                file=sys.stderr,
            )
            return False
        elif num_of_job_dsls > 1:
            # There should be no ambiguity in what job-dsl script to run.
            # That said, this is open to change.
            print(
                f"{PROGRAM_NAME}: {repo_name} has more than one job-dsl file, "
                "skip!",
                file=sys.stderr,
            )
            return False
        else:
            return True

    @staticmethod
    def _meets_casc_filereqs(repo_name, casc_files):
        """Check if the found casc file(s) meet the requirements.

        Should note this is solely program specific and not related to the
        limitations/restrictions of the JCasC plugin itself.

        Returns
        -------
        bool
            If all the casc file(s) meet the program requirements.

        """
        num_of_cascs = len(casc_files)
        if num_of_cascs == 0:
            print(
                f"{PROGRAM_NAME}: {repo_name} does not have a casc file!",
                file=sys.stderr,
            )
            return False
        elif num_of_cascs > 1:
            # There should be no ambiguity in what casc file is worked on.
            # This should not be opened to change considering another base
            # image could just be created.
            print(
                f"{PROGRAM_NAME}: {repo_name} has more than one casc file!",
                file=sys.stderr,
            )
            return False
        else:
            return True

    @staticmethod
    def _find_file_in_pwd(regex):
        """Locates files in the PWD using regex.

        Parameters
        ----------
        regex : str
            Regex to use for searching for file(s) in PWD.

        Returns
        -------
        files: list of str
            The files found.

        """
        regex = re.compile(regex)
        # While the func name assumes one file will be returned
        # its possible more than one can be returned.
        files = [file for file in os.listdir() if regex.search(file)]
        return files

    @classmethod
    def _expand_env_vars(cls, file, env_vars):
        """Evaluate env variables in the file.

        Parameters
        ----------
        file : str
            Represents the contents of a file.
        env_vars : list of str
            Env variable pairs, in the format of '<key>=<value>'.

        Returns
        -------
        str
            Same file but with env variables evaluated.

        Raises
        ------
        SystemExit
            If any of the env variable pairs passed in are invalid.

        """
        # will check for '<key>=<value>' format
        buffer = []
        env_var_names_to_values = dict()
        for env_var in env_vars:
            regex = re.compile(cls.ENV_VAR_REGEX)
            if regex.search(env_var):
                env_var_names_to_values[env_var.split("=")[0]] = env_var.split(
                    "="
                )[1]
            else:
                print(
                    f"{PROGRAM_NAME}: '{env_var}' env var is not formatted "
                    "correctly!",
                    file=sys.stderr,
                )
                sys.exit(1)

        for line in file.splitlines(keepends=True):
            line_env_vars = re.findall(cls.SHELL_VARIABLE_REGEX, line)
            modified_line = line
            if line_env_vars:
                # I do not want duplicate env vars recorded, overriding the env
                # var value works to my benefit here since each env var value
                # will be the same.
                line_env_var_names_to_env_vars = {
                    list(pair.keys())[0]: list(pair.values())[0]
                    for pair in list(
                        map(
                            lambda env_var: {
                                re.search(
                                    cls.SHELL_VARIABLE_NAME_REGEX, env_var
                                )[0]: env_var
                            },
                            line_env_vars,
                        )
                    )
                }
                for env_var_name in env_var_names_to_values.keys():
                    if env_var_name in line_env_var_names_to_env_vars:
                        modified_line = re.sub(
                            re.escape(
                                line_env_var_names_to_env_vars[env_var_name]
                            ),
                            env_var_names_to_values[env_var_name],
                            modified_line,
                        )
            buffer.append(modified_line)
        return "".join(buffer)

    @classmethod
    def retrieve_cmd_args(cls):
        """How arguments are retrieved from the command line.

        Returns
        -------
        Namespace
            An object that holds attributes pulled from the command line.

        Raises
        ------
        SystemExit
            If user input is not considered valid when parsing arguments.

        """

        def positive_int(string):
            """Determine if argument is a positive integer."""
            string_int = int(string)
            if not string_int > 0:
                raise ValueError
            return string_int

        try:
            # addjobs
            # max_help_position is increased (default is 24) to allow
            # arguments/options help messages be more indented, reference:
            # https://stackoverflow.com/questions/46554084/how-to-reduce-indentation-level-of-argument-help-in-argparse
            addjobs = cls._arg_subparsers.add_parser(
                cls.ADDJOBS_SUBCOMMAND,
                help=(
                    "will add Jenkins jobs to loaded configuration based on "
                    "job-dsl file(s) in repo(s)"
                ),
                formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
                parents=[cls._common_parser],
            )
            addjobs.add_argument(
                f"-{cls.TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION}",
                f"--{cls.TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME}",
                action="store_true",
                help=(
                    "transform readFileFromWorkspace functions to enable "
                    "usage with casc && job-dsl plugin"
                ),
            )

            # addagent-placeholder
            # TODO(cavcrosby): at moment, the normal subcommand is compared in main vs the cli name. Is there any reason not to just use the cli name?
            addagent_placeholder = cls._arg_subparsers.add_parser(
                cls.ADDAGENT_PLACEHOLDER_SUBCOMMAND_CLI_NAME,
                help=(
                    "will add a placeholder(s) for a new jenkins agent, to be "
                    "defined at run time"
                ),
                formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
                parents=[cls._common_parser],
            )
            addagent_placeholder.add_argument(
                f"-{cls.NUM_OF_AGENTS_TO_ADD_SHORT_OPTION}",
                f"--{cls.NUM_OF_AGENTS_TO_ADD_LONG_OPTION}",
                default=1,
                type=positive_int,
                help="number of agents (with their placeholders) to add",
            )

            # setup
            setup = cls._arg_subparsers.add_parser(
                cls.SETUP_SUBCOMMAND,
                help="invoked before running docker-build",
                formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
            )
            setup.add_argument(
                f"-{cls.CLEAN_SHORT_OPTION}",
                f"--{cls.CLEAN_LONG_OPTION}",
                action="store_true",
                help="clean PWD of the contents added by setup subcommand",
            )

            # docker-build
            docker_build = cls._arg_subparsers.add_parser(
                cls.DOCKER_BUILD_SUBCOMMAND_CLI_NAME,
                help="runs 'docker build'",
                formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
            )
            docker_build.add_argument(
                f"{cls.DOCKER_TAG_POSITIONAL_ARG}",
                metavar="TAG",
                help="this is to be a normal docker tag, or name:tag format",
            )
            docker_build.add_argument(
                f"-{cls.DOCKER_OPT_SHORT_OPTION}",
                f"--{cls.DOCKER_OPT_LONG_OPTION}",
                action="append",
                nargs="?",
                help=(
                    "passes options to 'docker build', e.g. [...] --opt "
                    "'-t image:v1.0.0' --opt '-t image:latest'"
                ),
            )
            docker_build.add_argument(
                f"-{cls.RELEASE_BUILD_SHORT_OPTION}",
                f"--{cls.RELEASE_BUILD_LONG_OPTION}",
                action="store_true",
                help="perform a docker build that is considered non-testing",
            )

            args = vars(cls._arg_parser.parse_args())
            return args
        except SystemExit:
            sys.exit(1)

    def _clone_git_repos(self, repo_urls, dest=os.getcwd()):
        """Fetch/clone git repos.

        These git repos will be placed into the directory PROJECTS_DIR_PATH.

        Parameters
        ----------
        repo_urls : list of str
            Git repo urls to make working copies of.
        dest : str, optional
            Destination path where the git repos will be
            cloned to (default is the PWD).

        Raises
        ------
        FileNotFoundError:
            If the git executable does not exist in the PATH.

        """
        # so I remember, finally always executes from try-except-else-finally
        try:
            if not pathlib.Path(dest).exists():
                os.mkdir(dest)
            os.chdir(dest)
            for repo_url in repo_urls:
                repo_name = os.path.basename(repo_url)
                subprocess.run(
                    ["git", "clone", "--quiet", repo_url, repo_name],
                    capture_output=True,
                    encoding="utf-8",
                    check=True,
                )
        except FileNotFoundError as e:
            print(
                f"{PROGRAM_NAME}: {e.filename} cannot be found in the PATH!",
                file=sys.stderr,
            )
            sys.exit(1)
        finally:
            os.chdir(PROGRAM_ROOT)

    def _load_vcs_repos(self):
        """How version source control (vcs) repos are loaded.

        Raises
        ------
        SystemExit
            If PROJECTS_DIR_PATH could not be found.

        """
        if pathlib.Path(self.PROJECTS_DIR_PATH).exists():
            os.chdir(self.PROJECTS_DIR_PATH)
            self.repo_names = os.listdir()
            os.chdir(PROGRAM_ROOT)
        else:
            # this means someone did not run the program 'setup' first
            print(
                f"{PROGRAM_NAME}: '{self.REPOS_TO_TRANSFER_DIR_NAME}' could "
                "not be found",
                file=sys.stderr,
            )
            sys.exit(1)

    def _load_casc(self, casc_path):
        """Load the casc required by the JCasC plugin.

        Usually this file is called 'casc.yaml' but can be set to something
        different depending on the CASC_FILENAME_REGEX.

        Parameters
        ----------
        casc_path : str
            Path of the casc file.

        Raises
        ------
        SystemExit
            If the casc file does not meet the casc file requirements.

        See Also
        --------
        CASC_FILENAME_REGEX

        """
        if casc_path is None:
            # By default, the base image's casc yaml will be loaded. The
            # yaml will be searched for, inspected, then the path to the
            # yaml file is set.
            os.chdir(self.DEFAULT_BASE_IMAGE_REPO_NAME)
            casc_files = self._find_file_in_pwd(self.CASC_FILENAME_REGEX)

            if not self._meets_casc_filereqs(
                self.DEFAULT_BASE_IMAGE_REPO_NAME, casc_files
            ):
                sys.exit(1)

            casc_file = casc_files[0]
            casc_path = os.path.join(
                PROGRAM_ROOT,
                self.DEFAULT_BASE_IMAGE_REPO_NAME,
                casc_file,
            )
            os.chdir(PROGRAM_ROOT)
        with open(casc_path, "r") as casc_target:
            self.casc = self._yaml_parser.load(casc_target)

    def _load_configs(self):
        """Load the program configuration file.

        Raises
        ------
        toml.decoder.TomlDecodeError
            If the configuration file loaded has a
            syntax error.

        """
        try:
            self.toml = toml.load(self.GIT_CONFIG_FILE_PATH)
        except toml.decoder.TomlDecodeError as e:
            print(
                f"{PROGRAM_NAME}: the configuration file contains syntax "
                "error(s):",
                file=sys.stderr,
            )
            print(e, file=sys.stderr)
            sys.exit(1)

    def _load_current_git_commit(self):
        """Grab the latest commit from the git repo.

        Assumes the PWD is in a git 'working' directory.

        Raises
        ------
        FileNotFoundError:
            If the git executable does not exist in the PATH.

        """
        # credits to:
        # https://stackoverflow.com/questions/11168141/find-which-commit-is-currently-checked-out-in-git#answer-42549385
        try:
            completed_process = subprocess.run(
                [
                    "git",
                    "show",
                    "--format=%h",
                    "--no-patch",
                ],
                capture_output=True,
                encoding="utf-8",
                check=True,
            )
            self.repo_commit = completed_process.stdout.strip()
        except FileNotFoundError as e:
            print(
                f"{PROGRAM_NAME}: {e.filename} cannot be found in the PATH!",
                file=sys.stderr,
            )
            sys.exit(1)

    def _load_current_git_branch(self):
        """Grab the current branch of the git repo.

        Assumes the PWD is in a git 'working' directory.

        Raises
        ------
        FileNotFoundError:
            If the git executable does not exist in the PATH.

        """
        try:
            completed_process = subprocess.run(
                [
                    "git",
                    "branch",
                    "--show-current",
                ],
                capture_output=True,
                encoding="utf-8",
                check=True,
            )
            self.repo_branch = completed_process.stdout.strip()
        except FileNotFoundError as e:
            print(
                f"{PROGRAM_NAME}: {e.filename} cannot be found in the PATH!",
                file=sys.stderr,
            )
            sys.exit(1)

    def _docker_build(self, tag, release, opts=None):
        """Run a preset docker build command.

        Should note only SIGINT is passed to the docker build process. This
        should be good enough but can be opened to pass in/handle more process
        signals.

        Parameters
        ----------
        tag : str
            This should be a docker tag, or 'name:tag'.
        release : bool
            Is this a 'release' build?
        opts : list of str, optional
            Options to be passed to the docker build subcommand
            (default is the None).

        Raises
        ------
        FileNotFoundError:
            If the docker executable does not exist in the PATH.

        Notes
        -----
        SIGSTOP according to the docs.python.org "...cannot be blocked.".
        Presuming this also means it cannot be caught either.

        """

        def sigint_handler(sigint, frame):

            docker_process.send_signal(sigint)

        if opts is None:
            opts = list()
        parsed_opts = [
            opt for opt_name_value in opts for opt in opt_name_value.split()
        ]

        docker_build = ["docker", "build"]
        # The '.' represents the path context (or contents) that is sent to
        # the docker daemon.
        if not release:
            docker_build += [
                "--no-cache",
                "--tag",
                tag,
            ]
            docker_build += parsed_opts
            docker_build += ["."]
        else:
            docker_build += [
                "--no-cache",
                "--build-arg",
                f"BRANCH={self.repo_branch}",
                "--build-arg",
                f"COMMIT={self.repo_commit}",
                "--tag",
                tag,
            ]
            docker_build += parsed_opts
            docker_build += ["."]

        try:
            docker_process = subprocess.Popen(
                docker_build,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                env=os.environ,
            )
        except FileNotFoundError as e:
            print(
                f"{PROGRAM_NAME}: {e.filename} cannot be found in the PATH!",
                file=sys.stderr,
            )
            sys.exit(1)

        # check to see if the docker build process has exited
        while docker_process.poll() is None:
            signal.signal(signal.SIGINT, sigint_handler)
        # docker_process.stderr itself is an _io.TextIOWrapper obj
        if docker_process.returncode != 0:
            raise subprocess.CalledProcessError(
                docker_process.returncode,
                docker_build,
                stderr=docker_process.stderr.read(),
            )

    def _merge_into_loaded_casc(self, casc_path):
        """Merge another casc file yaml with the loaded casc.

        Parameters
        ----------
        casc_path : str
            Name of the casc file to merge with loaded casc.

        Raises
        ------
        SystemExit
            If the casc path does not exist on the filesystem.

        """

        def __merge_into_loaded_casc_(casc_ptr, loaded_casc_ptr=self.casc):
            """Traverse the casc, merging it with the loaded casc."""
            for key in casc_ptr.keys():
                if loaded_casc_ptr.get(key, default=None) is None:
                    loaded_casc_ptr[key] = casc_ptr[key]
                elif isinstance(
                    loaded_casc_ptr[key], ruamel.yaml.comments.CommentedMap
                ):
                    # If the child node is also a parent node, we will want to
                    # iterate until we get to the bottom.
                    __merge_into_loaded_casc_(
                        casc_ptr[key], loaded_casc_ptr[key]
                    )
                else:
                    loaded_casc_ptr.update(casc_ptr)

        # 'as' variable name inspired from Python stdlib documentation:
        # https://docs.python.org/3/reference/compound_stmts.html#grammar-token-with-stmt
        with open(casc_path, "r") as casc_target:
            casc = self._yaml_parser.load(casc_target)
            __merge_into_loaded_casc_(casc)

    def _transform_rffw(self, repo_name, job_dsl):
        """Transform 'readFileFromWorkspace' expressions from job-dsl.

        Parameters
        ----------
        repo_name : str
            Name of the vcs repo.
        job_dsl : str
            Contents of a job-dsl.

        Returns
        -------
        job_dsl : str
            Same contents but with readFileFromWorkspace expressions
            transformed to be compatible with in a environment where
            Jenkins workspaces do not exist.

        """
        # assuming the job-dsl created also assumes the PWD == WORKSPACE
        def _transform_rffw_exp(rffw_exp):

            rffw_arg = re.search(
                self.READ_FILE_FROM_WORKSPACE_ARGUMENT_REGEX, rffw_exp
            )[0]
            t_rffw_arg = re.sub(
                self.PWD_IDENTIFIER_REGEX,
                f"./{self.REPOS_TO_TRANSFER_DIR_NAME}/{repo_name}/",
                rffw_arg,
            )
            # t_rffw_exp
            return (
                self.READ_FILE_FROM_WORKSPACE_EXPRESSION_REPLACEMENT.replace(
                    self.READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER,
                    t_rffw_arg,
                )
            )

        rffw_exps = dict()
        for rffw_exp in re.findall(
            self.READ_FILE_FROM_WORKSPACE_EXPRESSION_REGEX, job_dsl
        ):
            rffw_exps[rffw_exp] = _transform_rffw_exp(rffw_exp)
        # rffw_exp may need to have some characters escaped e.g. '(', ')', '.'
        for rffw_exp, t_rffw_exp in rffw_exps.items():
            job_dsl = re.sub(
                re.escape(rffw_exp),
                t_rffw_exp,
                job_dsl,
            )
        return job_dsl

    def _addagent_placeholder(self, num_of_agents):
        """Add specific Jenkins agent placeholders to be defined at runtime.

        This is allow images to define env vars for Jenkins agents without
        being explicit. Allowing the user to ignore the placeholders and to
        instantiate the Jenkins images without other Jenkins agents.

        Parameters
        ----------
        num_of_agents : int
            The number of agents to add to the casc used by JCasC.

        Notes
        -----
        Jenkins agents might also be called Jenkins 'nodes'. The term 'agent'
        will be used where possible to provide more distinction between the
        main (or master) Jenkins node vs a Jenkins agent.

        Below is an example of what is trying to be constructed through
        this function (assumes a pointer is at the list of nodes):

        - permanent:
            launcher:
               jnlp:
                 workDirSettings:
                   disabled: false
                   failIfWorkDirIsMissing: false
                   internalDir: "remoting"
            name: "foo"
            nodeDescription: "This is currently ran on the host..foo!"
            numExecutors: 2
            remoteFS: "/var/lib/jenkins-agents/mainnode1"
            retentionStrategy: "always"

        """
        casc_ptr = self.casc
        for index in reversed(range(1, num_of_agents + 1)):
            if self.JENKINS_ROOT_KEY_YAML not in casc_ptr:
                casc_ptr[self.JENKINS_ROOT_KEY_YAML] = []
            casc_ptr = casc_ptr[self.JENKINS_ROOT_KEY_YAML]
            if self.JENKINS_NODES_KEY_YAML not in casc_ptr:
                casc_ptr[self.JENKINS_NODES_KEY_YAML] = []
            casc_ptr = casc_ptr[self.JENKINS_NODES_KEY_YAML]
            casc_ptr.append(
                dict(
                    [
                        (
                            self.PERMANENT_KEY_YAML,
                            dict(
                                [
                                    (
                                        self.LAUNCHER_KEY_YAML,
                                        dict(
                                            [
                                                (
                                                    self.JNLP_KEY_YAML,
                                                    dict(
                                                        [
                                                            (
                                                                self.WORKDIRSETTINGS_KEY_YAML,
                                                                dict(
                                                                    [
                                                                        (
                                                                            self.DISABLED_KEY_YAML,
                                                                            "false",
                                                                        ),
                                                                        (
                                                                            self.FAIL_IF_WORKING_DIR_IS_MISSING_KEY_YAML,
                                                                            "false",
                                                                        ),
                                                                        (
                                                                            self.INTERNELDIR_KEY_YAML,
                                                                            "remoting",
                                                                        ),
                                                                    ]
                                                                ),
                                                            )
                                                        ]
                                                    ),
                                                )
                                            ]
                                        ),
                                    ),
                                    (
                                        self.NAME_KEY_YAML,
                                        f"${{{self.NAME_ENV_VAR_NAME}{index}}}",
                                    ),
                                    (
                                        self.NODE_DESCRIPTION_KEY_YAML,
                                        f"${{{self.NODE_DESCRIPTION_ENV_VAR_NAME}{index}}}",
                                    ),
                                    (
                                        self.NUM_EXECUTORS_KEY_YAML,
                                        f"${{{self.NUM_EXECUTORS_ENV_VAR_NAME}{index}}}",
                                    ),
                                    (
                                        self.REMOTEFS_KEY_YAML,
                                        f"${{{self.REMOTEFS_ENV_VAR_NAME}{index}}}",
                                    ),
                                    (
                                        self.RENTENTION_STRATEGY_KEY_YAML,
                                        "always",
                                    ),
                                ]
                            ),
                        )
                    ]
                )
            )

    def _addjobs(self, t_rffw):
        """Add job-dsl(s) to casc used by JCasC.

        Parameters
        ----------
        t_rffw : bool
            Whether or not to transform 'readFileFromWorkspace' (rffw)
            expressions from job-dsl(s).

        See Also
        --------
        _transform_rffw

        """
        os.chdir(self.PROJECTS_DIR_PATH)
        for repo_name in self.repo_names:
            try:
                os.chdir(repo_name)
                job_dsl_files = self._find_file_in_pwd(
                    self.JOB_DSL_FILENAME_REGEX
                )
                if not self._meets_job_dsl_filereqs(repo_name, job_dsl_files):
                    os.chdir("..")
                    continue

                job_dsl_file = job_dsl_files[0]
                with open(job_dsl_file, "r") as job_dsl_target:
                    job_dsl = job_dsl_target.read()
                if t_rffw:
                    job_dsl = self._transform_rffw(repo_name, job_dsl)

                # inspired from:
                # https://stackoverflow.com/questions/35433838/how-to-dump-a-folded-scalar-to-yaml-in-python-using-ruamel
                job_dsl_folded = scalarstring.FoldedScalarString(job_dsl)
                if self.JOB_DSL_ROOT_KEY_YAML not in self.casc:
                    self.casc[self.JOB_DSL_ROOT_KEY_YAML] = list()
                # dict([('sape', 4139)]) ==> {'sape': 4139}
                self.casc[self.JOB_DSL_ROOT_KEY_YAML].append(
                    dict([(self.JOB_DSL_SCRIPT_KEY_YAML, job_dsl_folded)])
                )
            finally:
                os.chdir(self.PROJECTS_DIR_PATH)
        # to re-establish being back at the project/program root
        os.chdir(PROGRAM_ROOT)

    def main(self, cmd_args):
        """Start the main program execution."""
        try:
            if cmd_args[self.SUBCOMMAND] == self.SETUP_SUBCOMMAND:
                self._load_configs()
                if cmd_args[self.CLEAN_LONG_OPTION]:
                    if pathlib.Path(self.PROJECTS_DIR_PATH).exists():
                        shutil.rmtree(self.PROJECTS_DIR_PATH)
                    if pathlib.Path(
                        self.DEFAULT_BASE_IMAGE_REPO_NAME
                    ).exists():
                        shutil.rmtree(self.DEFAULT_BASE_IMAGE_REPO_NAME)
                else:
                    self._clone_git_repos(
                        self.toml["git"]["repo_urls"],
                        dest=self.PROJECTS_DIR_PATH,
                    )
                    self._clone_git_repos([self.DEFAULT_BASE_IMAGE_REPO_URL])
            elif cmd_args[self.SUBCOMMAND] == self.ADDJOBS_SUBCOMMAND:
                self._load_vcs_repos()
                self._load_casc(
                    cmd_args[self.CASC_PATH_LONG_OPTION]
                )
                self._addjobs(
                    cmd_args[
                        self.TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION
                    ]
                )
                if cmd_args[self.MERGE_CASC_LONG_OPTION]:
                    self._merge_into_loaded_casc(
                        cmd_args[self.MERGE_CASC_LONG_OPTION]
                    )
                if cmd_args[self.ENV_VAR_LONG_OPTION]:
                    self._yaml_parser.dump(
                        self.casc,
                        self.DEFAULT_STDOUT_FD,
                        transform=(
                            lambda string: self._expand_env_vars(
                                string, cmd_args[self.ENV_VAR_LONG_OPTION]
                            )
                        ),
                    )
                else:
                    self._yaml_parser.dump(self.casc, self.DEFAULT_STDOUT_FD)
            elif (
                cmd_args[self.SUBCOMMAND]
                == self.ADDAGENT_PLACEHOLDER_SUBCOMMAND
            ):
                self._load_casc(
                    cmd_args[self.CASC_PATH_LONG_OPTION],
                )
                self._addagent_placeholder(
                    cmd_args[self.NUM_OF_AGENTS_TO_ADD_LONG_OPTION]
                )
                if cmd_args[self.MERGE_CASC_LONG_OPTION]:
                    self._merge_into_loaded_casc(
                        cmd_args[self.MERGE_CASC_LONG_OPTION]
                    )
                if cmd_args[self.ENV_VAR_LONG_OPTION]:
                    self._yaml_parser.dump(
                        self.casc,
                        self.DEFAULT_STDOUT_FD,
                        transform=(
                            lambda string: self._expand_env_vars(
                                string, cmd_args[self.ENV_VAR_LONG_OPTION]
                            )
                        ),
                    )
                else:
                    self._yaml_parser.dump(self.casc, self.DEFAULT_STDOUT_FD)
            elif cmd_args[self.SUBCOMMAND] == self.DOCKER_BUILD_SUBCOMMAND:
                self._load_current_git_branch()
                self._load_current_git_commit()
                self._docker_build(
                    cmd_args[self.DOCKER_TAG_POSITIONAL_ARG],
                    cmd_args[self.RELEASE_BUILD_LONG_OPTION],
                    cmd_args[self.DOCKER_OPT_LONG_OPTION],
                )
            sys.exit(0)
        except subprocess.CalledProcessError as e:
            # why yes, this is like the traceback.print_exception message!
            print(
                f"{PROGRAM_NAME}: cmd {e.cmd} returned non-zero exit status "
                f"{e.returncode}"
            )
            print(f"{PROGRAM_NAME}: cmd stderr: {e.stderr.strip()}")
            sys.exit(1)
        except FileNotFoundError as e:
            print(
                f"{PROGRAM_NAME}: could not find file: {e.filename}",
                file=sys.stderr,
            )
            sys.exit(1)
        except PermissionError as e:
            print(
                f"{PROGRAM_NAME}: a particular file/path was unaccessible, "
                f"{os.path.realpath(e)}",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            traceback.print_exception(
                type(e), e, e.__traceback__, file=sys.stderr
            )
            print(
                f"{PROGRAM_NAME}: an unknown error occurred, see the above!",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    jcasc = JenkinsConfigurationAsCode()
    args = jcasc.retrieve_cmd_args()
    jcasc.main(args)
