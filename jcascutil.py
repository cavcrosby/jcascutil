#!/usr/bin/env python3
"""A tool that works with configuration as code (CasC) files for Jenkins."""
# Standard Library Imports
import argparse
import os
import pathlib
import re
import shutil
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

# constants and other program configurations
_PROGRAM_NAME = os.path.basename(os.path.abspath(__file__))
_PROGRAM_ROOT = os.getcwd()
_arg_parser = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
        prog, max_help_position=35
    ),
    allow_abbrev=False,
)

YAML_PARSER = ruamel.yaml.YAML()
YAML_PARSER_WIDTH = 1000
YAML_PARSER.width = YAML_PARSER_WIDTH
REPOS_TO_TRANSFER_DIR_NAME = "projects"
DEFAULT_STDOUT_FD = sys.stdout
READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER = "__PLACEHOLDER__"
READ_FILE_FROM_WORKSPACE_EXPRESSION_REPLACEMENT = (
    f"new File('{READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER}').text"
)
DEFAULT_BASE_IMAGE_REPO_URL = (
    "https://github.com/cavcrosby/jenkins-docker-base"
)
DEFAULT_BASE_IMAGE_REPO_NAME = os.path.basename(DEFAULT_BASE_IMAGE_REPO_URL)
GIT_CONFIG_FILE_PATH = "./jobs.toml"
PROJECTS_DIR_PATH = f"{_PROGRAM_ROOT}/{REPOS_TO_TRANSFER_DIR_NAME}"

# regexes

JOB_DSL_FILENAME_REGEX = r".*job-dsl.*"
CASC_FILENAME_REGEX = r"^.*casc.*\.ya?ml$"
# readFileFromWorkspace('./foo')
READ_FILE_FROM_WORKSPACE_EXPRESSION_REGEX = (
    r"readFileFromWorkspace\(.+\)(?=\))"
)
# readFileFromWorkspace('./foo') ==> ./foo
READ_FILE_FROM_WORKSPACE_ARGUMENT_REGEX = (
    r"(?<=readFileFromWorkspace\(').+(?='\))"
)
PWD_IDENTIFIER_REGEX = r"\.\/"
SHELL_VARIABLE_REGEX = r"\$[a-zA-Z_]\w*$|\$\{{1}\w+\}{1}"
# assumes that any parsing will be on vars that are known shell variables
SHELL_VARIABLE_NAME_REGEX = r"(?<=\$\{)\w+(?=\})|(?<=\$)[a-zA-Z_]\w*"
ENV_VAR_REGEX = r"^[a-zA-Z_]\w*=.+"

# jenkins configurations as code (CasC) key values ({jenkins: {...}})

JOB_DSL_ROOT_KEY_YAML = "jobs"
JOB_DSL_SCRIPT_KEY_YAML = "script"
JENKINS_ROOT_KEY_YAML = "jenkins"
JENKINS_NODES_KEY_YAML = "nodes"
PERMANENT_KEY_YAML = "permanent"
LAUNCHER_KEY_YAML = "launcher"
JNLP_KEY_YAML = "jnlp"
WORKDIRSETTINGS_KEY_YAML = "workDirSettings"
DISABLED_KEY_YAML = "disabled"
FAIL_IF_WORKING_DIR_IS_MISSING_KEY_YAML = "failIfWorkDirIsMissing"
INTERNALDIR_KEY_YAML = "internalDir"
NAME_KEY_YAML = "name"
NODE_DESCRIPTION_KEY_YAML = "nodeDescription"
NUM_EXECUTORS_KEY_YAML = "numExecutors"
REMOTEFS_KEY_YAML = "remoteFS"
RENTENTION_STRATEGY_KEY_YAML = "retentionStrategy"

NAME_ENV_VAR_NAME = "JENKINS_AGENT_NAME"
NODE_DESCRIPTION_ENV_VAR_NAME = "JENKINS_AGENT_DESC"
NUM_EXECUTORS_ENV_VAR_NAME = "JENKINS_AGENT_NUM_EXECUTORS"
REMOTEFS_ENV_VAR_NAME = "JENKINS_AGENT_REMOTE_ROOT_DIR"

# subcommands labels

SUBCOMMAND = "subcommand"
ADDJOBS_SUBCOMMAND = "addjobs"
ADDAGENT_PLACEHOLDER_SUBCOMMAND = "addagent-placeholder"
SETUP_SUBCOMMAND = "setup"

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


def _meets_job_dsl_filereqs(repo_name, job_dsl_files):
    """Check if the found job-dsl files meet specific requirements.

    Returns
    -------
    bool
        If all the job-dsl file(s) meet the program requirements.

    Notes
    -----
    Should note this is solely program specific and not related to the
    limitations/restrictions of the job-dsl plugin itself.

    """
    num_of_job_dsls = len(job_dsl_files)
    if num_of_job_dsls == 0:
        print(
            f"{_PROGRAM_NAME}: {repo_name} does not have a job-dsl file, "
            "skip",
            file=sys.stderr,
        )
        return False
    elif num_of_job_dsls > 1:
        # There should be no ambiguity in what job-dsl script to run.
        # That said, this is open to change.
        print(
            f"{_PROGRAM_NAME}: {repo_name} has more than one job-dsl file, "
            "skip!",
            file=sys.stderr,
        )
        return False
    else:
        return True


def _meets_casc_filereqs(repo_name, casc_files):
    """Check if the found casc file(s) meet the requirements.

    Returns
    -------
    bool
        If all the casc file(s) meet the program requirements.

    Notes
    -----
    Should note this is solely program specific and not related to the
    limitations/restrictions of the JCasC plugin itself.

    """
    num_of_cascs = len(casc_files)
    if num_of_cascs == 0:
        print(
            f"{_PROGRAM_NAME}: {repo_name} does not have a casc file!",
            file=sys.stderr,
        )
        return False
    elif num_of_cascs > 1:
        # There should be no ambiguity in what casc file is worked on. This
        # should not be opened to change considering another base image could
        # just be created.
        print(
            f"{_PROGRAM_NAME}: {repo_name} has more than one casc file!",
            file=sys.stderr,
        )
        return False
    else:
        return True


def _find_files(regex):
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
    # While the func assumes one file will be returned its possible more than
    # one can be returned.
    files = [file for file in os.listdir() if regex.search(file)]
    return files


def _expand_env_vars(file, env_vars):
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
        Same file contents but with env variables evaluated.

    Raises
    ------
    SystemExit
        If any of the env variable pairs passed in are invalid.

    """
    # will check for '<key>=<value>' format
    env_var_names_to_values = dict()
    for env_var in env_vars:
        regex = re.compile(ENV_VAR_REGEX)
        if regex.search(env_var):
            env_var_names_to_values[env_var.split("=")[0]] = env_var.split(
                "="
            )[1]
        else:
            print(
                f"{_PROGRAM_NAME}: '{env_var}' env var is not formatted "
                "correctly!",
                file=sys.stderr,
            )
            sys.exit(1)

    buffer = []
    for line in file.splitlines(keepends=True):
        line_env_vars = re.findall(SHELL_VARIABLE_REGEX, line)
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
                            re.search(SHELL_VARIABLE_NAME_REGEX, env_var)[
                                0
                            ]: env_var
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


def retrieve_cmd_args():
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
        addjobs = _arg_subparsers.add_parser(
            ADDJOBS_SUBCOMMAND,
            help=(
                "will add Jenkins jobs to loaded configuration based on "
                "job-dsl file(s) in repo(s)"
            ),
            formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                prog, max_help_position=35
            ),
            allow_abbrev=False,
            parents=[_common_parser],
        )
        addjobs.add_argument(
            f"-{TRANSFORM_READ_FILE_FROM_WORKSPACE_SHORT_OPTION}",
            f"--{TRANSFORM_READ_FILE_FROM_WORKSPACE_CLI_NAME}",
            action="store_true",
            help=(
                "transform readFileFromWorkspace functions to enable "
                "usage with casc && job-dsl plugin"
            ),
        )

        # addagent-placeholder
        addagent_placeholder = _arg_subparsers.add_parser(
            ADDAGENT_PLACEHOLDER_SUBCOMMAND,
            help=(
                "will add a placeholder(s) for a new jenkins agent, to be "
                "defined at run time"
            ),
            formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                prog, max_help_position=35
            ),
            allow_abbrev=False,
            parents=[_common_parser],
        )
        addagent_placeholder.add_argument(
            f"-{NUM_OF_AGENTS_TO_ADD_SHORT_OPTION}",
            f"--{NUM_OF_AGENTS_TO_ADD_LONG_OPTION}",
            default=1,
            type=positive_int,
            help="number of agents (with their placeholders) to add",
        )

        # setup
        setup = _arg_subparsers.add_parser(
            SETUP_SUBCOMMAND,
            help="invoked before running docker-build",
            formatter_class=lambda prog: CustomRawDescriptionHelpFormatter(
                prog, max_help_position=35
            ),
            allow_abbrev=False,
        )
        setup.add_argument(
            f"-{CLEAN_SHORT_OPTION}",
            f"--{CLEAN_LONG_OPTION}",
            action="store_true",
            help="clean PWD of the contents added by setup subcommand",
        )

        args = vars(_arg_parser.parse_args())
        return args
    except SystemExit:
        sys.exit(1)


def _clone_git_repos(repo_urls, dest=os.getcwd()):
    """Fetch/clone git repos.

    Parameters
    ----------
    repo_urls : list of str
        Git repo urls to make working copies of.
    dest : str, optional
        Destination path where the git repos will be
        cloned to.

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
            f"{_PROGRAM_NAME}: {e.filename} cannot be found in the PATH!",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        os.chdir(_PROGRAM_ROOT)


def _get_vcs_repos():
    """Get the vcs repo names.

    Returns
    -------
    repo_names : list of str
        Version source control (e.g. git, mercurial) repo names.

    Raises
    ------
    SystemExit
        If PROJECTS_DIR_PATH could not be found.

    """
    if pathlib.Path(PROJECTS_DIR_PATH).exists():
        os.chdir(PROJECTS_DIR_PATH)
        repo_names = os.listdir()
        os.chdir(_PROGRAM_ROOT)
        return repo_names
    else:
        # this means someone did not run the program 'setup' first
        print(
            f"{_PROGRAM_NAME}: '{REPOS_TO_TRANSFER_DIR_NAME}' could "
            "not be found",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_casc(casc_path):
    """Load the casc contents.

    Parameters
    ----------
    casc_path : str
        Path of the casc file.

    Returns
    -------
    ruamel.yaml.comments.CommentedMap
        The casc file contents.

    Raises
    ------
    SystemExit
        If the casc file does not meet the casc file requirements.

    See Also
    --------
    CASC_FILENAME_REGEX

    Notes
    -----
    Usually this file is called 'casc.yaml' but can be set to something
    different depending on the CASC_FILENAME_REGEX.

    """
    if casc_path is None:
        # By default, the base image's casc yaml will be loaded. The
        # yaml will be searched for, inspected, then the path to the
        # yaml file is set.
        os.chdir(DEFAULT_BASE_IMAGE_REPO_NAME)
        casc_files = _find_files(CASC_FILENAME_REGEX)

        # DISCUSS(cavcrosby): the following func just checks to make sure only
        # one casc file exists in the base image repo. Has nothing todo with
        # the actual casc file or contents itself.
        if not _meets_casc_filereqs(DEFAULT_BASE_IMAGE_REPO_NAME, casc_files):
            sys.exit(1)

        casc_file = casc_files[0]
        casc_path = os.path.join(
            _PROGRAM_ROOT,
            DEFAULT_BASE_IMAGE_REPO_NAME,
            casc_file,
        )
        os.chdir(_PROGRAM_ROOT)
    with open(casc_path, "r") as casc_target:
        return YAML_PARSER.load(casc_target)


def _load_configs():
    """Load the program configuration file.

    Returns
    -------
    dict
        The program configuration file contents.

    Raises
    ------
    toml.decoder.TomlDecodeError
        If the configuration file loaded has a
        syntax error.

    """
    try:
        return toml.load(GIT_CONFIG_FILE_PATH)
    except toml.decoder.TomlDecodeError as e:
        print(
            f"{_PROGRAM_NAME}: the configuration file contains syntax "
            "error(s):",
            file=sys.stderr,
        )
        print(e, file=sys.stderr)
        sys.exit(1)


def _merge_casc(casc_path, into):
    """Merge a casc file with another casc file's contents.

    Parameters
    ----------
    casc_path : str
        Path of the casc file to merge.
    into : ruamel.yaml.comments.CommentedMap
        The casc file contents who we wish to merge into.

    Raises
    ------
    SystemExit
        If the casc path does not exist on the filesystem.

    """

    def __merge_casc_(casc_ptr, into_ptr=into):
        """Traverse the casc, merging it with the other casc."""
        for key in casc_ptr.keys():
            if into_ptr.get(key, default=None) is None:
                into_ptr[key] = casc_ptr[key]
            elif isinstance(into_ptr[key], ruamel.yaml.comments.CommentedMap):
                # If the child node is also a parent node, we will want to
                # iterate until we get to the bottom.
                __merge_casc_(casc_ptr[key], into_ptr[key])
            else:
                into_ptr.update(casc_ptr)

    # 'as' variable name inspired from Python stdlib documentation:
    # https://docs.python.org/3/reference/compound_stmts.html#grammar-token-with-stmt
    with open(casc_path, "r") as casc_target:
        casc = YAML_PARSER.load(casc_target)
        __merge_casc_(casc)


def _transform_rffw(repo_name, job_dsl):
    """Transform 'readFileFromWorkspace' expressions in job-dsl.

    Parameters
    ----------
    repo_name : str
        Name of the vcs repo.
    job_dsl : str
        Contents of a job-dsl.

    Returns
    -------
    job_dsl : str
        Same contents but with readFileFromWorkspace expressions transformed to
        be compatible with a environment where Jenkins workspaces do not
        initally exist.

    """
    # assuming the job-dsl created also assumes the PWD == WORKSPACE
    def _transform_rffw_exp(rffw_exp):

        rffw_arg = re.search(
            READ_FILE_FROM_WORKSPACE_ARGUMENT_REGEX, rffw_exp
        )[0]
        t_rffw_arg = re.sub(
            PWD_IDENTIFIER_REGEX,
            f"./{REPOS_TO_TRANSFER_DIR_NAME}/{repo_name}/",
            rffw_arg,
        )
        # t_rffw_exp
        return READ_FILE_FROM_WORKSPACE_EXPRESSION_REPLACEMENT.replace(
            READ_FILE_FROM_WORKSPACE_ARGUMENT_PLACEHOLDER,
            t_rffw_arg,
        )

    rffw_exps = dict()
    for rffw_exp in re.findall(
        READ_FILE_FROM_WORKSPACE_EXPRESSION_REGEX, job_dsl
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


def _addagent_placeholder(num_of_agents, casc):
    """Add specific Jenkins agent placeholders to be defined at runtime.

    Parameters
    ----------
    num_of_agents : int
        The number of agent placeholders to add to the casc.
    casc : ruamel.yaml.comments.CommentedMap
        The casc file contents.

    Notes
    -----
    This is allow images to define env vars for Jenkins agents without being
    explicit. This also means the user can choose to ignore the placeholders
    and to instantiate the Jenkins images without other Jenkins agents.

    Jenkins agents might also be called Jenkins 'nodes'. The term 'agent' will
    be used where possible to provide more distinction between the main
    (or master) Jenkins node vs a Jenkins agent.

    Below is an example of what is trying to be constructed through this
    function (assumes a pointer is at the list of nodes):

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
        remoteFS: "/var/lib/jenkins-agent/main-node1"
        retentionStrategy: "always"

    """
    casc_ptr = casc
    if JENKINS_ROOT_KEY_YAML not in casc_ptr:
        casc_ptr[JENKINS_ROOT_KEY_YAML] = []
    casc_ptr = casc_ptr[JENKINS_ROOT_KEY_YAML]
    if JENKINS_NODES_KEY_YAML not in casc_ptr:
        casc_ptr[JENKINS_NODES_KEY_YAML] = []
    casc_ptr = casc_ptr[JENKINS_NODES_KEY_YAML]
    for index in range(1, num_of_agents + 1):
        casc_ptr.append(
            dict(
                [
                    (
                        PERMANENT_KEY_YAML,
                        dict(
                            [
                                (
                                    LAUNCHER_KEY_YAML,
                                    dict(
                                        [
                                            (
                                                JNLP_KEY_YAML,
                                                dict(
                                                    [
                                                        (
                                                            WORKDIRSETTINGS_KEY_YAML,
                                                            dict(
                                                                [
                                                                    (
                                                                        DISABLED_KEY_YAML,
                                                                        "false",
                                                                    ),
                                                                    (
                                                                        FAIL_IF_WORKING_DIR_IS_MISSING_KEY_YAML,
                                                                        "false",
                                                                    ),
                                                                    (
                                                                        INTERNALDIR_KEY_YAML,
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
                                    NAME_KEY_YAML,
                                    f"${{{NAME_ENV_VAR_NAME}{index}}}",
                                ),
                                (
                                    NODE_DESCRIPTION_KEY_YAML,
                                    f"${{{NODE_DESCRIPTION_ENV_VAR_NAME}{index}}}",
                                ),
                                (
                                    NUM_EXECUTORS_KEY_YAML,
                                    f"${{{NUM_EXECUTORS_ENV_VAR_NAME}{index}}}",
                                ),
                                (
                                    REMOTEFS_KEY_YAML,
                                    f"${{{REMOTEFS_ENV_VAR_NAME}{index}}}",
                                ),
                                (
                                    RENTENTION_STRATEGY_KEY_YAML,
                                    "always",
                                ),
                            ]
                        ),
                    )
                ]
            )
        )


def _addjobs(t_rffw, repo_names, casc):
    """Add job-dsl(s) to casc.

    Parameters
    ----------
    t_rffw : bool
        Whether or not to transform 'readFileFromWorkspace' (rffw) expressions
        from job-dsl(s).
    repo_names : list of str
        Version source control (e.g. git, mercurial) repo names.
    casc : ruamel.yaml.comments.CommentedMap
        The casc file contents.

    See Also
    --------
    _transform_rffw

    """
    os.chdir(PROJECTS_DIR_PATH)
    for repo_name in repo_names:
        try:
            os.chdir(repo_name)
            job_dsl_files = _find_files(JOB_DSL_FILENAME_REGEX)
            # DISCUSS(cavcrosby): the following func just checks to make sure
            # only one job-dsl file exists in the repo. Has nothing todo with
            # the actual job-dsl file or contents itself.
            if not _meets_job_dsl_filereqs(repo_name, job_dsl_files):
                os.chdir("..")
                continue

            job_dsl_file = job_dsl_files[0]
            with open(job_dsl_file, "r") as job_dsl_target:
                job_dsl = job_dsl_target.read()
            if t_rffw:
                job_dsl = _transform_rffw(repo_name, job_dsl)

            # inspired from:
            # https://stackoverflow.com/questions/35433838/how-to-dump-a-folded-scalar-to-yaml-in-python-using-ruamel
            job_dsl_folded = scalarstring.FoldedScalarString(job_dsl)
            if JOB_DSL_ROOT_KEY_YAML not in casc:
                casc[JOB_DSL_ROOT_KEY_YAML] = list()
            # dict([('sape', 4139)]) ==> {'sape': 4139}
            casc[JOB_DSL_ROOT_KEY_YAML].append(
                dict([(JOB_DSL_SCRIPT_KEY_YAML, job_dsl_folded)])
            )
        finally:
            os.chdir(PROJECTS_DIR_PATH)
    # to re-establish being back at the project/program root
    os.chdir(_PROGRAM_ROOT)


def main(args):
    """Start the main program execution."""
    try:
        if args[SUBCOMMAND] == SETUP_SUBCOMMAND:
            configs = _load_configs()
            if args[CLEAN_LONG_OPTION]:
                if pathlib.Path(PROJECTS_DIR_PATH).exists():
                    shutil.rmtree(PROJECTS_DIR_PATH)
                if pathlib.Path(DEFAULT_BASE_IMAGE_REPO_NAME).exists():
                    shutil.rmtree(DEFAULT_BASE_IMAGE_REPO_NAME)
            else:
                _clone_git_repos(
                    configs["git"]["repo_urls"],
                    dest=PROJECTS_DIR_PATH,
                )
                _clone_git_repos([DEFAULT_BASE_IMAGE_REPO_URL])
        elif (
            args[SUBCOMMAND] == ADDJOBS_SUBCOMMAND
            or args[SUBCOMMAND]  # noqa: W503
            == ADDAGENT_PLACEHOLDER_SUBCOMMAND  # noqa: W503
        ):
            casc = _load_casc(args[CASC_PATH_LONG_OPTION])
            if args[SUBCOMMAND] == ADDJOBS_SUBCOMMAND:
                repo_names = _get_vcs_repos()
                _addjobs(
                    args[TRANSFORM_READ_FILE_FROM_WORKSPACE_LONG_OPTION],
                    repo_names,
                    casc,
                )
            if args[SUBCOMMAND] == ADDAGENT_PLACEHOLDER_SUBCOMMAND:
                _addagent_placeholder(
                    args[NUM_OF_AGENTS_TO_ADD_LONG_OPTION], casc
                )
            if args[MERGE_CASC_LONG_OPTION]:
                _merge_casc(args[MERGE_CASC_LONG_OPTION], into=casc)
            if args[ENV_VAR_LONG_OPTION]:
                YAML_PARSER.dump(
                    casc,
                    DEFAULT_STDOUT_FD,
                    transform=(
                        lambda string: _expand_env_vars(
                            string, args[ENV_VAR_LONG_OPTION]
                        )
                    ),
                )
            else:
                YAML_PARSER.dump(casc, DEFAULT_STDOUT_FD)
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        # why yes, this is like the traceback.print_exception message!
        print(
            f"{_PROGRAM_NAME}: cmd {e.cmd} returned non-zero exit status "
            f"{e.returncode}"
        )
        print(f"{_PROGRAM_NAME}: cmd stderr: {e.stderr.strip()}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(
            f"{_PROGRAM_NAME}: could not find file: {e.filename}",
            file=sys.stderr,
        )
        sys.exit(1)
    except PermissionError as e:
        print(
            f"{_PROGRAM_NAME}: a particular file/path was unaccessible, "
            f"{os.path.realpath(e)}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
        print(
            f"{_PROGRAM_NAME}: an unknown error occurred, see the above!",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    args = retrieve_cmd_args()
    main(args)
    sys.exit(0)
