# generate-jobs-yaml.py

This script/program was created to enable the ability to be able to create different Jenkins docker containers for each set of jobs. This is meant to be used along with the [job-dsl-plugin](https://wiki.jenkins.io/display/JENKINS/Job+DSL+Plugin) and [Jenkins Configuration as Code (a.k.a. JCasC) Plugin](https://plugins.jenkins.io/configuration-as-code/).

Currently this script reads a "jobs.toml" file (for an example of this, see https://github.com/reap2sow1/jenkins-docker-torkel.git) for git repos to clone. From there, for each repo, the script attempts to find a job-dsl file at the root of the git repo. Each job-dsl file is read and then stitched them together into a yaml "document", later to be conjoined to the yaml used by the JCasC plugin. Below is an example of what's expected to be crafted as output.

*NOTE: The library used by the script does not preserve indentation at least individually for each line. The intent is not so much for human readability at this point, so that is ok. For reference, see: (https://yaml.readthedocs.io/en/latest/overview.html).*

```yaml
jobs:
- script: >
    freeStyleJob ('packerbuilds') {

        concurrentBuild(false) 

        logRotator {
            numToKeep(10)
            artifactNumToKeep(10)
        }

        [...]
    }
- script: >
    freeStyleJob ('foo') {
        [...]
    }
```
