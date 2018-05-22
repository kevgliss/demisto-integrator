demisto-integrator
==================

Simple CLI that helps combining demisto-content with your own custom content.

Useful in instances where you have your own custom repository of integrations but there are
supported integrations you would like to add and recieve updates for.


Installation
------------

Via pipenv (see: https://docs.pipenv.org/):

    pipenv install demisto-integrator


Usage
-----

    integrator sync --help

*Note:* `--custom-content-repo` will be created for you if it doesn't exist. `integrator` will also attempt
to push to remote if configured.

Configuration
-------------

`demisto-integrator` uses a `.contentignore` file to suppress the addition and updating of unwanted files.

`.contentignore` patterns are compatible with https://git-scm.com/docs/gitignore.

A sample `.contentignore`:

```bash
*.sh
*.py
License/*
Tests/*
TestPlay
```

