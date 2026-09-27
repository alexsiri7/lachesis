# Repository Contents

## Purpose

Agents working with the author, not only the worker that writes the code, need to read what is in a repository: to audit code against its spec, to understand a failure, or to answer a question from the worker. This capability gives read-only access to the files of registered repositories.

## Requirements

### Requirement: Files can be read

The system SHALL return the content of a file in a registered repository, from the default branch or from a named branch, tag or commit. For a file too large to return whole, it SHALL return the start of it and say that it was cut short and how large the file is.

#### Scenario: Reading source for an audit
- GIVEN a registered repository with a file src/backlog.py
- WHEN that file is read
- THEN its content on the default branch is returned

#### Scenario: Reading a pull request's version
- GIVEN an open pull request whose branch changes src/backlog.py
- WHEN the file is read at that branch
- THEN the branch's version is returned

### Requirement: Directories can be listed

The system SHALL list the entries of a directory in a registered repository, at the same choice of branch, tag or commit, saying for each entry whether it is a file or a directory.

#### Scenario: Finding the tests
- GIVEN a repository with a tests directory
- WHEN it is listed
- THEN every file and subdirectory in it is returned with its kind

### Requirement: Reading never writes

Reading repository contents SHALL NOT change the repository, and SHALL be refused for repositories that are not registered.

#### Scenario: Unregistered repository
- GIVEN a repository that is not registered
- WHEN one of its files is read
- THEN the read is refused and the caller is told the repository is not registered
