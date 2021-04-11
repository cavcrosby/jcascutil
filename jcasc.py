#!/usr/bin/env python3
# Standard Library Imports
import subprocess
import shutil
import sys
import re
import signal
import traceback
import os
import pathlib
import argparse
from os.path import realpath

# Third Party Imports
import ruamel.yaml
import toml
from ruamel.yaml.scalarstring import FoldedScalarString as folded

# Local Application Imports

# general program configurations

PROGRAM_NAME = os.path.basename(os.path.abspath(__file__))
PROGRAM_ROOT = os.getcwd()


class CustomHelpFormatter(argparse.HelpFormatter):
    """A custom HelpFormatter subclass used by argparse.ArgumentParser objects.

    Main change from the original argparse.HelpFormatter has where
    the format of the option string(s) with its argument
    has changed. See 'NOTE' below.

    """

    def _format_action_invocation(self, action):
        if not action.option_strings:
            default = self._get_default_metavar_for_positional(action)
            (metavar,) = self._metavar_formatter(action, default)(1)
            return metavar

        else:
            parts = []

            # if the Optional doesn't take a value, format is:
            #    -s, --long
            if action.nargs == 0:
                parts.extend(action.option_strings)

            # NOTE: if the Optional takes a value, formats are:
            #    -s, --long=ARG ==> if both short/long
            #    --long=ARG ==> if just long
            #    -s=ARG ==> if just short
            else:
                default = self._get_default_metavar_for_optional(action)
                args_string = self._format_args(action, default)
                for option_string in action.option_strings:
                    if option_string == action.option_strings[-1]:
                        parts.append(f"{option_string}={args_string}")
                    else:
                        parts.append(option_string)

            return ", ".join(parts)


class JenkinsConfigurationAsCode:
    """A small utility to aid in the construction of Jenkins images/containers.

    From a high level, the functionality implemented is to allow Jenkins
    images to be created with differing jobs, all in thanks to the
    job-dsl plugin. Also the JCasC plugin is in use to automate the Jenkins
    installation on to the containers. Finally, Docker containers are the types
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
    OTHER_PROGRAMS_NEEDED : list of str
        Other programs on the PATH needed by this program.

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
    READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER = "_PLACEHOLDER"
    READ_FILE_FROM_WORKSPACE_EXPRESSION_REPLACEMENT = (
        f"new File('{READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER}').text"
    )
    PWD_IDENTIFER_REGEX = r"\.\/"

    # class specific misc

    REPOS_TO_TRANSFER_DIR_NAME = "projects"
    DEFAULT_STDOUT_FD = sys.stdout
    YAML_PARSER_WIDTH = 1000
    OTHER_PROGRAMS_NEEDED = ["git", "docker"]
    # should mention this does not cover edge case of
    # using '_' as the variable name, should be ok
    ENV_VAR_REGEX = r"^[a-zA-Z_]\w*=.+"

    # repo configurations

    DEFAULT_BASE_IMAGE_REPO_URL = (
        "https://github.com/reap2sow1/jenkins-docker-base"
    )
    DEFAULT_BASE_IMAGE_REPO_NAME = os.path.basename(
        DEFAULT_BASE_IMAGE_REPO_URL
    )
    GIT_CONFIG_FILE_PATH = "./jobs.toml"
    PROJECTS_DIR_PATH = f"{PROGRAM_ROOT}/{REPOS_TO_TRANSFER_DIR_NAME}"

    # subcommands labels

    # replace(old, new)
    SUBCOMMAND = "subcommand"
    ADDJOBS_SUBCOMMAND = "addjobs"
    ADDNODE_PLACEHOLDER_SUBCOMMAND = "addnode-placeholder"
    ADDNODE_PLACEHOLDER_SUBCOMMAND_CLI_NAME = (
        ADDNODE_PLACEHOLDER_SUBCOMMAND.replace("_", "-")
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
    # as long as the short optional argument is not part
    # of the same subcommand, then it is ok
    CLEAN_SHORT_OPTION = "c"
    CLEAN_LONG_OPTION = "clean"
    CASC_PATH_LONG_OPTION_CLI_NAME = CASC_PATH_LONG_OPTION.replace("_", "-")
    ENV_VAR_SHORT_OPTION = "e"
    ENV_VAR_LONG_OPTION = "env"
    MERGE_YAML_SHORT_OPTION = "m"
    MERGE_YAML_LONG_OPTION = "merge_yaml"
    MERGE_YAML_CLI_NAME = MERGE_YAML_LONG_OPTION.replace("_", "-")
    NUM_OF_NODES_TO_ADD_SHORT_OPTION = "n"
    NUM_OF_NODES_TO_ADD_LONG_OPTION = "numnodes"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION = "t"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION = "transform_rffw"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME = (
        TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION.replace("_", "-")
    )
    DOCKER_TAG_POSITIONAL_ARG = "tag"
    DOCKER_OPT_SHORT_OPTION = "o"
    DOCKER_OPT_LONG_OPTION = "opt"
    OFFICIAL_BUILD_SHORT_OPTION = "b"
    OFFICIAL_BUILD_LONG_OPTION = "officialbld"

    _DESC = """Description: A small utility to aid in the construction of Jenkins containers."""
    _arg_parser = argparse.ArgumentParser(
        description=_DESC,
        formatter_class=lambda prog: CustomHelpFormatter(
            prog, max_help_position=35
        ),
        allow_abbrev=False,
    )
    _arg_subparsers = _arg_parser.add_subparsers(
        title=f"{SUBCOMMAND}s",
        metavar=f"{SUBCOMMAND}s [options ...]",
        dest=SUBCOMMAND,
    )
    _arg_subparsers.required = True

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
        """Checks if the found job-dsl files meet specific requirements.

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
                f"{PROGRAM_NAME}: {repo_name} does not have a job-dsl file, skip",
                file=sys.stderr,
            )
            return False
        elif num_of_job_dsls > 1:
            # there should be no ambiguity in what job-dsl script to run
            # NOTE: this is open to change
            print(
                f"{PROGRAM_NAME}: {repo_name} has more than one job-dsl file, skip!",
                file=sys.stderr,
            )
            return False
        else:
            return True

    @staticmethod
    def _meets_casc_filereqs(repo_name, casc_files):
        """Checks if the found casc files meet specific requirements.

        Should note this is solely program specific and not
        related to the limitations/restrictions of the JCasC plugin itself.

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
            # there should be no ambiguity in what casc file is worked on
            # NOTE: this shouldn't be as opened to change considering another
            # base image could just be created
            print(
                f"{PROGRAM_NAME}: {repo_name} has more than one casc!",
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

        Raises
        ------
        subprocess.CalledProcessError
            Incase the program used to find files has an issue.

        """
        regex = re.compile(regex)
        # NOTE: while the func name assumes one file will be returned
        # its possible more than one can be returned
        files = [f for f in os.listdir() if regex.search(f)]
        return files

    @classmethod
    def _expand_env_vars(cls, fc, env_vars):
        """How env variables are expanded for file-contents.

        Parameters
        ----------
        fc : str
            Represents the contents of a file.
        env_vars : list of str
            Env variable pairs, in the format of '<key>=<value>' strs.

        Returns
        -------
        str
            Same file-contents but with env variables evaluated.

        Raises
        ------
        SystemExit
            If any of the env variable pairs passed in are invalid.

        """
        # will check for '<key>=<value>' format
        for env_var in env_vars:
            regex = re.compile(cls.ENV_VAR_REGEX)
            if regex.search(env_var):
                os.environ[f"{env_var.split('=')[0]}"] = env_var.split("=")[1]
            else:
                print(
                    f"{PROGRAM_NAME}: '{env_var}' env var is not formatted correctly!",
                    file=sys.stderr,
                )
                sys.exit(1)

        return os.path.expandvars(fc)

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

        def positive_int(s):
            """Used as an argument to 'type' for ints > 0."""
            # string_int
            s_i = int(s)
            if not s_i > 0:
                raise ValueError
            return s_i

        try:
            # addjobs
            # NOTE: max_help_position is increased (default is 24)
            # to allow arguments/options help messages be more indented
            # reference:
            # https://stackoverflow.com/questions/46554084/how-to-reduce-indentation-level-of-argument-help-in-argparse
            addjobs = cls._arg_subparsers.add_parser(
                cls.ADDJOBS_SUBCOMMAND,
                help=f"will add Jenkins jobs to loaded configuration based on job-dsl file(s) in repo(s)",
                formatter_class=lambda prog: CustomHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
            )
            addjobs.add_argument(
                f"-{cls.CASC_PATH_SHORT_OPTION}",
                f"--{cls.CASC_PATH_LONG_OPTION_CLI_NAME}",
                help="load custom casc instead from CASC_JENKINS_CONFIG",
                metavar="CASC_PATH",
            )
            addjobs.add_argument(
                f"-{cls.TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION}",
                f"--{cls.TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME}",
                action="store_true",
                help="transform readFileFromWorkspace functions to enable usage with casc && job-dsl plugin",
            )
            addjobs.add_argument(
                f"-{cls.ENV_VAR_SHORT_OPTION}",
                f"--{cls.ENV_VAR_LONG_OPTION}",
                nargs="*",
                help="set environment variables, format: '<key>=<value>'",
            )
            addjobs.add_argument(
                f"-{cls.MERGE_YAML_SHORT_OPTION}",
                f"--{cls.MERGE_YAML_CLI_NAME}",
                help="merge yaml into loaded casc",
                metavar="YAML_PATH",
            )

            # TODO(conner@conneracrosby.tech): Move arguments for duplicate cli arguments to class variables
            # TODO(conner@conneracrosby.tech): See about if exception print messages are consistent
            # TODO(conner@conneracrosby.tech): Add more comments in very condensed code, to improve readability...I'm looking at you main!!

            # addnode-placeholder
            addnode_placeholder = cls._arg_subparsers.add_parser(
                cls.ADDNODE_PLACEHOLDER_SUBCOMMAND_CLI_NAME,
                help=f"will add a placeholder(s) for a new jenkins node, to be defined at run time",
                formatter_class=lambda prog: CustomHelpFormatter(
                    prog, max_help_position=35
                ),
                allow_abbrev=False,
            )
            addnode_placeholder.add_argument(
                f"-{cls.NUM_OF_NODES_TO_ADD_SHORT_OPTION}",
                f"--{cls.NUM_OF_NODES_TO_ADD_LONG_OPTION}",
                default=1,
                type=positive_int,
                help="number of nodes (with their placeholders) to add",
            )
            addnode_placeholder.add_argument(
                f"-{cls.CASC_PATH_SHORT_OPTION}",
                f"--{cls.CASC_PATH_LONG_OPTION_CLI_NAME}",
                help="load custom casc instead from CASC_JENKINS_CONFIG",
                metavar="CASC_PATH",
            )
            addnode_placeholder.add_argument(
                f"-{cls.ENV_VAR_SHORT_OPTION}",
                f"--{cls.ENV_VAR_LONG_OPTION}",
                nargs="*",
                help="set environment variables, format: '<key>=<value>'",
            )
            addnode_placeholder.add_argument(
                f"-{cls.MERGE_YAML_SHORT_OPTION}",
                f"--{cls.MERGE_YAML_CLI_NAME}",
                help="merge yaml into loaded casc",
                metavar="YAML_PATH",
            )

            # setup
            setup = cls._arg_subparsers.add_parser(
                cls.SETUP_SUBCOMMAND,
                help="invoked before running docker-build",
                allow_abbrev=False,
            )
            setup.add_argument(
                f"-{cls.CLEAN_SHORT_OPTION}",
                f"--{cls.CLEAN_LONG_OPTION}",
                action="store_true",
                help="clean PWD of the contents added by setup subcommand",
            )

            # docker-build
            # NOTE: assumes this script is invoked in a vsc repo that contains
            # the dockerfile used to construct the docker Jenkins image
            # NOTE2: aka, the PWD is the context sent to the docker daemon
            # NOTE3: Branch/commit info will be based on the current context
            # of the repo
            docker_build = cls._arg_subparsers.add_parser(
                cls.DOCKER_BUILD_SUBCOMMAND_CLI_NAME,
                help="runs 'docker build'",
                formatter_class=lambda prog: CustomHelpFormatter(
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
                help="passes options to 'docker build', e.g. [...] --opt '-t image:v1.0.0' --opt '-t image:latest' ",
            )
            docker_build.add_argument(
                f"-{cls.OFFICIAL_BUILD_SHORT_OPTION}",
                f"--{cls.OFFICIAL_BUILD_LONG_OPTION}",
                action="store_true",
                help="perform a docker build that is considered non-testing",
            )

            args = vars(cls._arg_parser.parse_args())
            return args
        except SystemExit:
            sys.exit(1)

    @classmethod
    def _have_other_programs(cls):
        """Checks if certain programs can be found on the PATH.

        Returns
        -------
        have_other_programs : bool
            If all the specified programs could be found.

        See Also
        --------
        OTHER_PROGRAMS_NEEDED

        """
        # TODO(conner@conneracrosby.tech): on the PATH or in the PATH?
        have_other_programs = True
        for prog in cls.OTHER_PROGRAMS_NEEDED:
            if shutil.which(prog) is None:
                print(
                    f"{PROGRAM_NAME}: {prog} cannot be found on the PATH!",
                    file=sys.stderr,
                )
                have_other_programs = False

        return have_other_programs

    def _clone_git_repos(self, repo_urls, dst=os.getcwd()):
        """Fetches/clones git repos.

        These git repos will be placed into the directory PROJECTS_DIR_PATH.
        Makes use of the client git program.

        Parameters
        ----------
        repo_urls : list of str
            Git repo urls to make working copies of.
        dst : str, optional
            Destination path where the git repos will be
            cloned to (default is the PWD).

        Raises
        ------
        subprocess.CalledProcessError
            If the git client program has issues when
            running.
        PermissionError
            If the user running the command does not have write
            permissions to dst.

        """
        # so I remember, finally always executes
        # from try-except-else-finally block.
        try:
            if not pathlib.Path(dst).exists():
                os.mkdir(dst)
            os.chdir(dst)
            for repo_url in repo_urls:
                repo_name = os.path.basename(repo_url)
                completed_process = subprocess.run(
                    ["git", "clone", "--quiet", repo_url, repo_name],
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    encoding="utf-8",
                    check=True,
                )
        except subprocess.CalledProcessError:
            print(completed_process.stderr.strip(), file=sys.stderr)
            raise
        except PermissionError:
            raise
        finally:
            os.chdir("..")

    def _load_git_repos(self):
        """How git repos are loaded.

        Raises
        ------
        SystemExit
            If PROJECTS_DIR_PATH could not be found.

        """
        if pathlib.Path(self.PROJECTS_DIR_PATH).exists():
            os.chdir(self.PROJECTS_DIR_PATH)
            self.repo_names = os.listdir()
            os.chdir("..")
        else:
            # either this means someone did not run the program setup first,
            # or docker somehow missed COPY'ing to the image
            print(
                f"{PROGRAM_NAME}: '{self.REPOS_TO_TRANSFER_DIR_NAME}' could not be found",
                file=sys.stderr,
            )
            sys.exit(1)

    def _load_casc(self, casc_path, env_vars):
        """How the yaml required by the JCasC plugin is loaded.

        Usually this is called 'casc.yaml' but can be set to something
        different depending on the CASC_FILENAME_REGEX.

        Parameters
        ----------
        casc_path : str
            Path of the casc file.

        Raises
        ------
        SystemExit
            If the casc file does not exist on the filesystem,
            based on the passed in path.

        See Also
        --------
        CASC_FILENAME_REGEX

        """
        try:
            if casc_path is None:
                # NOTE: by default, the base image's casc yaml
                # will be loaded. At this point, the repo should have been
                # cloned. Then the yaml will be searched for, inspected, then
                # the path to the yaml file is set.
                os.chdir(self.DEFAULT_BASE_IMAGE_REPO_NAME)
                casc_filenames = self._find_file_in_pwd(
                    self.CASC_FILENAME_REGEX
                )
                if not self._meets_casc_filereqs(
                    self.DEFAULT_BASE_IMAGE_REPO_NAME, casc_filenames
                ):
                    os.chdir("..")
                    sys.exit(1)
                casc_filename = casc_filenames[0]
                casc_path = os.path.join(
                    PROGRAM_ROOT,
                    self.DEFAULT_BASE_IMAGE_REPO_NAME,
                    casc_filename,
                )
            with open(casc_path, "r") as yaml_f:
                if env_vars:
                    casc_fc = yaml_f.read()
                    self.casc = self._yaml_parser.load(
                        self._expand_env_vars(casc_fc, env_vars)
                    )
                else:
                    self.casc = self._yaml_parser.load(yaml_f)
        except FileNotFoundError:
            print(
                f"{PROGRAM_NAME}: casc file could not be found at:",
                file=sys.stderr,
            )
            print(casc_path, file=sys.stderr)
            sys.exit(1)

    def _load_toml(self):
        """How toml files are loaded for the program.

        Raises
        ------
        toml.decoder.TomlDecodeError
            If the configuration file loaded has a
            syntax error.

        """
        # TODO(conner@conneracrosby.tech): generalize configholder and make personal stdlib?
        try:
            self.toml = toml.load(self.GIT_CONFIG_FILE_PATH)
        except PermissionError:
            raise
        except toml.decoder.TomlDecodeError as e:
            print(
                f"{PROGRAM_NAME}: the configuration file contains syntax error(s), more details below",
                file=sys.stderr,
            )
            print(e, file=sys.stderr)
            sys.exit(1)

    def _load_current_git_commit(self):
        """Grabs the latest commit from the git repo.

        Assumes the PWD is in a git 'working' directory.

        """
        # credits to:
        # https://stackoverflow.com/questions/11168141/find-which-commit-is-currently-checked-out-in-git#answer-42549385
        completed_process = subprocess.run(
            [
                "git",
                "show",
                "--format=%h",
                "--no-patch",
            ],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf-8",
            check=True,
        )
        self.repo_commit = completed_process.stdout.strip()

    def _load_current_git_branch(self):
        """Grabs the current branch from the git repo.

        Assumes the PWD is in a git 'working' directory.

        """
        completed_process = subprocess.run(
            [
                "git",
                "branch",
                "--show-current",
            ],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf-8",
            check=True,
        )
        self.repo_branch = completed_process.stdout.strip()

    def _docker_build(self, tag, officialbld, opts=None):
        """Runs a preset docker build command.

        Should note only SIGINT is passed to the docker build
        process. This should be ok but can be opened to
        pass in/handle more process signals.

        Parameters
        ----------
        tag : str
            This should be a docker tag, or 'name:tag'.
        officialbld : bool
            Is this an 'official' build?
        opts : list of str, optional
            Options to be passed to the docker build
            subcommand (default is the None).

        Notes
        -----
        SIGSTOP according to the docs.python.org "...cannot be blocked.".
        Assuming this also means it cannot be caught either.

        """

        def sigint_handler(sigint, frame):

            docker_process.send_signal(sigint)

        # needed, else TypeError occurs
        if opts is None:
            opts = list()
        # parsed opts
        p_opts = [
            opt for opt_name_value in opts for opt in opt_name_value.split()
        ]

        docker_bldcmd = ["docker", "build"]
        # the '.' represents the path context (or contents) that are sent
        # to the docker daemon
        if not officialbld:
            docker_bldcmd += [
                "--no-cache",
                "--tag",
                tag,
            ]
            docker_bldcmd += p_opts
            docker_bldcmd += ["."]
        else:
            docker_bldcmd += [
                "--no-cache",
                "--build-arg",
                f"BRANCH={self.repo_branch}",
                "--build-arg",
                f"COMMIT={self.repo_commit}",
                "--tag",
                tag,
            ]
            docker_bldcmd += p_opts
            docker_bldcmd += ["."]

        docker_process = subprocess.Popen(
            docker_bldcmd,
            env=os.environ,
        )
        
        # check to see if docker_process has exited
        while docker_process.poll() is None:
            signal.signal(signal.SIGINT, sigint_handler)

    def _merge_into_loaded_casc(self, yaml_path):
        """Merges yaml with the loaded casc.

        Parameters
        ----------
        yaml_path : str
            Name of yaml file to merge with loaded casc.

        Raises
        ------
        SystemExit
            If the yaml file does not exist on the filesystem.

        """

        def __merge_into_loaded_casc_(yaml_ptr, casc_ptr=self.casc):

            for key in yaml_ptr.keys():
                # casc currently doesn't have this key and its children
                # (including if the children are also parent nodes), just graft
                # into the casc
                if casc_ptr.get(key, default=None) is None:
                    # e.g.
                    # [...]
                    #   key: yaml_ptr[key]
                    #   [...]
                    # [...]
                    casc_ptr[key] = yaml_ptr[key]
                elif isinstance(
                    casc_ptr[key], ruamel.yaml.comments.CommentedMap
                ):
                    # NOTE: if the child node is also a parent node,
                    # we will want to iterate until we get to the bottom
                    __merge_into_loaded_casc_(yaml_ptr[key], casc_ptr[key])
                else:
                    # the original casc has this key,
                    # so just update key and children
                    casc_ptr.update(yaml_ptr)

        try:
            with open(yaml_path, "r") as yaml_f:
                yaml = self._yaml_parser.load(yaml_f)
                __merge_into_loaded_casc_(yaml)
        except FileNotFoundError:
            print(
                f"{PROGRAM_NAME}: yaml file to merge could not be found at:",
                file=sys.stderr,
            )
            print(yaml_path, file=sys.stderr)
            sys.exit(1)

    def _transform_rffw(self, repo_name, job_dsl_fc):
        """Transforms 'readFileFromWorkspace' expressions from job-dsl(s).

        Parameters
        ----------
        repo_name : str
            Name of the vcs repo.
        job_dsl_fc : str
            Contents of the job-dsl as a str.

        Returns
        -------
        job_dsl_fc : str
            Same contents but with readFileFromWorkspace
            expressions transformed into something different to
            be compatible with the docker Jenkins image.

        """
        # assuming the job-dsl created also assumes the PWD == WORKSPACE
        def _transform_rffw_exp(rffw_exp):

            regex = re.compile(self.READ_FILE_FROM_WORKSPACE_ARGUMENT_REGEX)
            rffw_arg = regex.search(rffw_exp)[0]
            t_rffw_arg = re.sub(
                self.PWD_IDENTIFER_REGEX,
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
            self.READ_FILE_FROM_WORKSPACE_EXPRESSION_REGEX, job_dsl_fc
        ):
            rffw_exps[rffw_exp] = _transform_rffw_exp(rffw_exp)
        # rffw_exp may need to have some characters escaped
        # e.g. '(', ')', '.'
        for rffw_exp, t_rffw_exp in rffw_exps.items():
            job_dsl_fc = re.sub(
                re.escape(rffw_exp),
                t_rffw_exp,
                job_dsl_fc,
            )
        return job_dsl_fc

    def _addnode_placeholder(self, index):
        """Adds specific Jenkins node placeholders to be defined at runtime.

        This is allow images to define env vars for another Jenkins node
        without being explicit. Allowing the user to ignore the placeholders
        and to instantiate the Jenkins images without other Jenkins nodes.

        Parameters
        ----------
        index : int
            The number of nodes to add to the yaml used by JCasC.

        Notes
        -----
        Below is an example of what is trying to be constructed through
        this function (assumes a pointer is at the list of nodes):

        - permanent:
            launcher:
               jnlp:
                 workDirSettings:
                   disabled: false
                   failIfWorkDirIsMissing: false
                   internalDir: "remoting"
            name: "foo-host"
            nodeDescription: "This is currently ran on the host..foo!"
            numExecutors: 2
            remoteFS: "/var/lib/jenkins-nodes/foo-host"
            retentionStrategy: "always"

        """
        general_casc_ptr = self.casc
        if index != 0:
            self._addnode_placeholder(index - 1)
        else:
            if self.JENKINS_ROOT_KEY_YAML not in general_casc_ptr:
                general_casc_ptr[self.JENKINS_ROOT_KEY_YAML] = []
            general_casc_ptr = general_casc_ptr[self.JENKINS_ROOT_KEY_YAML]
            if self.JENKINS_NODES_KEY_YAML not in general_casc_ptr:
                general_casc_ptr[self.JENKINS_NODES_KEY_YAML] = []
            return
        general_casc_ptr = general_casc_ptr[self.JENKINS_ROOT_KEY_YAML]
        general_casc_ptr = general_casc_ptr[self.JENKINS_NODES_KEY_YAML]
        general_casc_ptr.append(
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
                                (self.RENTENTION_STRATEGY_KEY_YAML, "always"),
                            ]
                        ),
                    )
                ]
            )
        )

    def _addjobs(self, t_rffw):
        """Adds job-dsl(s) content(s) to yaml used by JCasC.

        Parameters
        ----------
        t_rffw : bool
            Whether or not to transform 'readFileFromWorkspace' expressions
            from job-dsl(s).

        See Also
        --------
        _transform_rffw

        """
        os.chdir(self.PROJECTS_DIR_PATH)
        for repo_name in self.repo_names:
            try:
                os.chdir(repo_name)
                job_dsl_filenames = self._find_file_in_pwd(
                    self.JOB_DSL_FILENAME_REGEX
                )
                if not self._meets_job_dsl_filereqs(
                    repo_name, job_dsl_filenames
                ):
                    os.chdir("..")
                    continue
                job_dsl_filename = job_dsl_filenames[0]

                # read in the job_dsl file, fc == filecontents
                with open(job_dsl_filename, "r") as job_dsl_fh:
                    job_dsl_fc = job_dsl_fh.read()
                if t_rffw:
                    job_dsl_fc = self._transform_rffw(repo_name, job_dsl_fc)
                # TODO(conner@conneracrosby.tech): Add for ability for 'file' entry to be added vs script' ???
                # NOTE: inspired from:
                # https://stackoverflow.com/questions/35433838/how-to-dump-a-folded-scalar-to-yaml-in-python-using-ruamel
                # ffc == foldedfile-contents
                job_dsl_ffc = folded(job_dsl_fc)
                # NOTE2: this handles the situation for multiple job-dsls:
                # create the 'JOB_DSL_SCRIPT_KEY_YAML: job_dsl_ffc' then
                # either merge into JOB_DSL_ROOT_KEY_YAML
                # or create the JOB_DSL_ROOT_KEY_YAML entry and append to it
                if self.JOB_DSL_ROOT_KEY_YAML in self.casc:
                    self.casc[self.JOB_DSL_ROOT_KEY_YAML].append(
                        dict([(self.JOB_DSL_SCRIPT_KEY_YAML, job_dsl_ffc)])
                    )
                else:
                    script_entry = dict(
                        [(self.JOB_DSL_SCRIPT_KEY_YAML, job_dsl_ffc)]
                    )
                    self.casc[self.JOB_DSL_ROOT_KEY_YAML] = [script_entry]
            except PermissionError:
                raise
            finally:
                os.chdir(PROGRAM_ROOT)

    def main(self, cmd_args):
        """The main of the program."""
        if not self._have_other_programs():
            # this should not be a big enough issue to fail out, as
            # some scenarios might not need all executables
            # e.g. docker is not needed when running addjobs in a
            # constructing image
            pass
        try:
            self._load_toml()
            if cmd_args[self.SUBCOMMAND] == self.SETUP_SUBCOMMAND:
                if pathlib.Path(self.PROJECTS_DIR_PATH).exists():
                    shutil.rmtree(self.PROJECTS_DIR_PATH)
                if pathlib.Path(self.DEFAULT_BASE_IMAGE_REPO_NAME).exists():
                    shutil.rmtree(self.DEFAULT_BASE_IMAGE_REPO_NAME)
                if not cmd_args[self.CLEAN_LONG_OPTION]:
                    # clones repos to be used by job-dsl at container runtime
                    self._clone_git_repos(
                        self.toml["git"]["repo_urls"], self.PROJECTS_DIR_PATH
                    )
                    # NOTE: fetches the base Jenkins image, mainly used to get
                    # yaml that is used to automate the install of Jenkins
                    self._clone_git_repos([self.DEFAULT_BASE_IMAGE_REPO_URL])
            elif cmd_args[self.SUBCOMMAND] == self.ADDJOBS_SUBCOMMAND:
                self._load_git_repos()
                self._load_casc(
                    cmd_args[self.CASC_PATH_LONG_OPTION],
                    cmd_args[self.ENV_VAR_LONG_OPTION],
                )
                self._addjobs(
                    cmd_args[
                        self.TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION
                    ]
                )
                if cmd_args[self.MERGE_YAML_LONG_OPTION]:
                    self._merge_into_loaded_casc(
                        cmd_args[self.MERGE_YAML_LONG_OPTION]
                    )
                self._yaml_parser.dump(self.casc, self.DEFAULT_STDOUT_FD)
            elif (
                cmd_args[self.SUBCOMMAND]
                == self.ADDNODE_PLACEHOLDER_SUBCOMMAND
            ):
                self._load_casc(
                    cmd_args[self.CASC_PATH_LONG_OPTION],
                    cmd_args[self.ENV_VAR_LONG_OPTION],
                )
                self._addnode_placeholder(
                    cmd_args[self.NUM_OF_NODES_TO_ADD_LONG_OPTION]
                )
                if cmd_args[self.MERGE_YAML_LONG_OPTION]:
                    self._merge_into_loaded_casc(
                        cmd_args[self.MERGE_YAML_LONG_OPTION]
                    )
                self._yaml_parser.dump(self.casc, self.DEFAULT_STDOUT_FD)
            elif cmd_args[self.SUBCOMMAND] == self.DOCKER_BUILD_SUBCOMMAND:
                self._load_current_git_branch()
                self._load_current_git_commit()
                self._docker_build(
                    cmd_args[self.DOCKER_TAG_POSITIONAL_ARG],
                    cmd_args[self.OFFICIAL_BUILD_LONG_OPTION],
                    cmd_args[self.DOCKER_OPT_LONG_OPTION],
                )
            sys.exit(0)
        except (subprocess.CalledProcessError):
            sys.exit(1)
        except PermissionError as e:
            print(
                f"{PROGRAM_NAME}: a particular file/path was unaccessible, {realpath(e)}",
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
