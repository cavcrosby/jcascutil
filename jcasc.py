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

PROGRAM_NAME = os.path.dirname(os.path.abspath(__file__))
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
    JOB_DSL_FILENAME_REGEX = ".*job-dsl.*"
    # based on the actual env var the casc plugin looks for
    # ...referring to variable name
    CASC_JENKINS_CONFIG_ENV_VAR = "CASC_JENKINS_CONFIG"
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
    OTHER_PROGRAMS_NEEDED = ["git", "find", "docker"]
    # should mention this does not cover edge case of
    # using '_' as the variable name, should be ok
    ENV_VAR_REGEX = r"^[a-zA-Z_]\w*=.+"

    # repo configurations

    GIT_CONFIG_FILE_PATH = "./jobs.toml"
    GIT_REPOS_DIR_PATH = f"{PROGRAM_ROOT}/{REPOS_TO_TRANSFER_DIR_NAME}"

    # subcommands labels

    # replace(old, new)
    SUBCOMMAND = "subcommand"
    ADDJOBS_SUBCOMMAND = "addjobs"
    SETUP_SUBCOMMAND = "setup"
    DOCKER_BUILD_SUBCOMMAND = "docker-build"
    DOCKER_BUILD_SUBCOMMAND_CLI_NAME = "docker_build".replace("_", "-")

    # positional/optional argument labels
    # used at the command line and to reference values of arguments

    CASC_PATH_SHORT_OPTION = "c"
    CASC_PATH_LONG_OPTION = "casc_path"
    CASC_PATH_LONG_OPTION_CLI_NAME = CASC_PATH_LONG_OPTION.replace("_", "-")
    ENV_VAR_SHORT_OPTION = "e"
    ENV_VAR_LONG_OPTION = "env"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION = "t"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION = "transform_rffw"
    TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME = (
        TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION.replace("_", "-")
    )
    DOCKER_TAG_POSITIONAL_ARG = "tag"
    OFFICIAL_BUILD_SHORT_OPTION = "b"
    OFFICIAL_BUILD_LONG_OPTION = "officialbld"

    _DESC = """Description: A small utility to aid in the construction of Jenkins containers."""
    _arg_parser = argparse.ArgumentParser(
        description=_DESC,
        allow_abbrev=False,
    )
    _arg_subparsers = _arg_parser.add_subparsers(
        title=f"{SUBCOMMAND}s",
        metavar=f"{SUBCOMMAND}s [options ...]",
        dest=SUBCOMMAND,
    )
    _arg_subparsers.required = True

    def __init__(self):

        self.repo_urls = None
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
            If all the job-dsl files meet the program requirements.

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

            # setup
            setup = cls._arg_subparsers.add_parser(
                cls.SETUP_SUBCOMMAND,
                help="invoked before running docker-build",
                allow_abbrev=False,
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
                allow_abbrev=False,
            )
            docker_build.add_argument(
                f"{cls.DOCKER_TAG_POSITIONAL_ARG}",
                metavar="TAG",
                help="this is to be a normal docker tag, or name:tag format",
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

    @classmethod
    def _find_job_dsl_file(cls):
        """Locates job-dsl files in the PWD using regex.

        Returns
        -------
        job_dsl_files: list of str
            The job-dsl files found.

        Raises
        ------
        subprocess.CalledProcessError
            Incase the program used to find job-dsl files has an issue.

        See Also
        --------
        JOB_DSL_FILENAME_REGEX

        """
        completed_process = subprocess.run(
            [
                "find",
                ".",
                "-regextype",
                "sed",
                "-maxdepth",
                "1",
                "-regex",
                cls.JOB_DSL_FILENAME_REGEX,
            ],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf-8",
            check=True,
        )
        # everything but the last index, as it is just ''
        # NOTE: while the func name assumes one file will be returned
        # its possible more than one can be returned
        job_dsl_files = completed_process.stdout.split("\n")[:-1]
        return job_dsl_files

    def _clone_git_repos(self):
        """Fetches/clones git repos.

        These git repos will be placed into the directory GIT_REPOS_DIR_PATH.
        Makes use of the client git program.

        Raises
        ------
        subprocess.CalledProcessError
            If the git client program has issues when
            running.
        PermissionError
            If the user running the command does not have write
            permissions to GIT_REPOS_DIR_PATH.

        See Also
        --------
        GIT_REPOS_DIR_PATH

        """
        self.repo_urls = self.toml["git"]["repo_urls"]

        if pathlib.Path(self.GIT_REPOS_DIR_PATH).exists():
            shutil.rmtree(self.GIT_REPOS_DIR_PATH)

        # so I remember, finally always executes
        # from try-except-else-finally block.
        try:
            os.mkdir(self.GIT_REPOS_DIR_PATH)
            os.chdir(self.GIT_REPOS_DIR_PATH)
            for repo_url in self.repo_urls:
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
            If GIT_REPOS_DIR_PATH could not be found.

        """
        if pathlib.Path(self.GIT_REPOS_DIR_PATH).exists():
            os.chdir(self.GIT_REPOS_DIR_PATH)
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
        different depending on the CASC_JENKINS_CONFIG_ENV_VAR.

        Parameters
        ----------
        casc_path : str
            Path of the casc file.

        Raises
        ------
        SystemExit
            If the casc file does not exist on the filesystem,
            based on the passed in path, or if the CASC_JENKINS_CONFIG_ENV_VAR
            does not exist in the current env.

        """
        try:
            if casc_path is None:
                casc_path = os.environ[self.CASC_JENKINS_CONFIG_ENV_VAR]
            with open(casc_path, "r") as yaml_f:
                if env_vars:
                    casc_fc = yaml_f.read()
                    self.casc = self._yaml_parser.load(
                        self._expand_env_vars(casc_fc, env_vars)
                    )
                else:
                    self.casc = self._yaml_parser.load(yaml_f)
        except TypeError:
            print(
                f"{PROGRAM_NAME}: casc file could not be found at:",
                file=sys.stderr,
            )
            print(casc_path, file=sys.stderr)
            sys.exit(1)
        except KeyError:
            print(
                f"{PROGRAM_NAME}: {self.CASC_JENKINS_CONFIG_ENV_VAR} does not exist in the current env",
                file=sys.stderr,
            )
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

    def _docker_build(self, tag, officialbld):
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

        Notes
        -----
        SIGSTOP according to the docs.python.org "...cannot be blocked.".
        Assuming this also means it cannot be caught either.

        """

        def sigint_handler(sigint, frame):

            docker_process.send_signal(sigint)

        docker_bldcmd = ["docker", "build"]
        # the '.' represents the path context (or contents) that are sent
        # to the docker daemon
        if not officialbld:
            docker_bldcmd += [
                "--no-cache",
                "--tag",
                tag,
                ".",
            ]
        else:
            docker_bldcmd += [
                "--no-cache",
                "--build-arg",
                f"BRANCH={self.repo_branch}",
                "--build-arg",
                f"COMMIT={self.repo_commit}",
                "--tag",
                tag,
                ".",
            ]
        docker_process = subprocess.Popen(
            docker_bldcmd,
            env=os.environ,
        )

        # check to see if docker_process has exited
        while docker_process.poll() is None:
            signal.signal(signal.SIGINT, sigint_handler)

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
        os.chdir(self.GIT_REPOS_DIR_PATH)
        for repo_name in self.repo_names:
            try:
                os.chdir(repo_name)
                job_dsl_filenames = self._find_job_dsl_file()
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
                if pathlib.Path(self.GIT_REPOS_DIR_PATH).exists():
                    shutil.rmtree(self.GIT_REPOS_DIR_PATH)
                self._clone_git_repos()
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
                self._yaml_parser.dump(self.casc, self.DEFAULT_STDOUT_FD)
            elif cmd_args[self.SUBCOMMAND] == self.DOCKER_BUILD_SUBCOMMAND:
                self._load_current_git_branch()
                self._load_current_git_commit()
                self._docker_build(
                    cmd_args[self.DOCKER_TAG_POSITIONAL_ARG],
                    cmd_args[self.OFFICIAL_BUILD_LONG_OPTION],
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
