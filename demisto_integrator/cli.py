import os
import re
import sys
import stat
import logging
import fnmatch
import difflib
from datetime import date
from shutil import copyfile

import click
import click_log

from dulwich.repo import Repo
from dulwich.errors import NotGitRepository
from dulwich.porcelain import clone, open_repo_closing, commit, tag_create, tag_list

from ._version import __version__

logger = logging.getLogger(__name__)
click_log.basic_config(logger)

DEMISTO_CONTENT_URL = 'git@github.com:demisto/content.git'
DEMISTO_CONTENT_DIR = os.path.join(os.getcwd(), 'demisto-content')


class IgnoredFiles(object):
    """
      Understand ignored files/folders (from .gitignore and/or .svnignore)
      Allows to avoid walking through build and config folders
    """

    def __init__(self, path, basename='.contentignore', use_default_ignores=True):
        """
          :param str path: Path to folder we want to know ignore
          :param str basename: Basename of the "ignore file"
          :param bool use_default_ignores: Auto-add all usual ignores? (ie: .git/ .gradle/ etc)
        """
        self.root = os.path.abspath(path)
        self.invalid = []
        self._regexes = set()
        self.basename = basename

        self.parse_gitignore(os.path.join(os.path.dirname(self.root), basename))
        if use_default_ignores:
            self.add('.git/')

    def __repr__(self):
        return '%s ignores, %s invalid' % (len(self._regexes), len(self.invalid))

    def __len__(self):
        return len(self._regexes)

    def match(self, path, is_dir=None):
        """
          :param str path: Full path to file
          :param bool|None is_dir: If applicable, caller can indicate whether 'full_path' is a directory or not (to save a file stat call)
          :return IgnorePattern|None: Pattern that leads to 'path' being ignored, if any
        """
        for regex in self._regexes:
            if regex.match(path, is_dir=is_dir):
                return regex

        return None

    def show_ignores(self):
        """
          Useful for debugging, show which files would be ignored in self.root, and why (due to which pattern)
        """
        result = []

        for root, dirs, files in os.walk(self.root):
            for basename in dirs[:]:
                fpath = os.path.join(root, basename)
                relative_path = fpath[len(self.root) + 1:]
                m = self.match(fpath, is_dir=True)
                if m:
                    dirs.remove(basename)
                    result.append("%-30s: %s" % (m.description, relative_path))

            for basename in files:
                fpath = os.path.join(root, basename)
                relative_path = fpath[len(self.root) + 1:]
                m = self.match(fpath, is_dir=False)
                if m:
                    result.append("%-30s: %s" % (m.description, relative_path))

        return '\n'.join(result)

    def parse_gitignore(self, path):
        """
          Add ignores as defined in .gitignore file with 'path'
          :param str path: Path to .gitignore file
        """
        try:
            if not os.path.exists(path):
                self.invalid.append("No folder %s" % path)
                return

            line_number = 0
            with open(path, 'r') as fh:
                for line in fh:
                    line_number += 1
                    line = line.strip()

                    if not line or line[0] == '#':
                        continue

                    if line[0] == '!':
                        # Negation patterns not supported... if .gitignore is uber crazy, then too bad, we possibly won't find all .py files in such esoteric setups
                        self.invalid.append("Negation pattern line %s not supported" % line_number)
                        continue

                    self.add(line, line_number)

        except Exception as e:
            self.invalid.append('Crashed: %s' % e)

    def remove_ignored_folders(self, root, dirs):
        """
          Remove all 'names' that should be ignored, handy for use with os.walk
          :param str root: Parent of 'dirs'
          :param list dirs: List of dirs to remove ignored folders (as defined in this object) from
        """
        for basename in dirs[:]:
            if self.match(os.path.join(root, basename), is_dir=True):
                dirs.remove(basename)

    def add(self, pattern, line_number=0):
        """
          :param str pattern: Pattern to ignore
          :param int line_number: Line number in .gitignore file
        """
        pat = IgnorePattern(self.root, pattern, self.basename, line_number)

        if pat.invalid:
            self.invalid.append(pat)

        else:
            self._regexes.add(pat)


class IgnorePattern(object):
    """
      Represents a .gitignore pattern, compatible with https://git-scm.com/docs/gitignore
    """

    def __init__(self, root, pattern, basename, line_number):
        """
          :param str root: Folder containing ignore file
          :param str pattern: Pattern from ignore file
          :param str basename: Basename of ignore file
          :param int line_number: Line number in ignore file
        """
        self.root = root
        self.pattern = pattern
        self.description = '%s:%2s:%s' % (basename, line_number, pattern)
        self.invalid = None
        self.applies_to_directories = False  # When True, this pattern applies to directories only (not files or symlinks)
        self.match_basename = False  # When True, match against filename (otherwise: relative path)
        self.exact_match = None  # Exact string to match (no regex needed)
        self.regex = None  # Regex to use

        if pattern.endswith('/') and not pattern.endswith('*/'):
            # Anything ending with '/' simply means pattern applies to directories only
            self.applies_to_directories = True
            pattern = pattern[:-1]

        if pattern.startswith('**/'):
            pattern = pattern[3:]
            if self._has_glob(pattern):
                self.invalid = "Too complex"
                return

            if not pattern:
                self.match_basename = True
                self.regex = re.compile('.*')

            elif '/' in pattern:
                # **/foo/bar
                self.match_basename = False
                self.regex = re.compile('.*/%s' % re.escape(pattern))

            else:
                # **/foo is the same as ignoring basename foo
                self.match_basename = True
                self.exact_match = pattern

            return

        if pattern.endswith('/**'):
            pattern = pattern[:-3]
            if self._has_glob(pattern):
                self.invalid = "Too complex"
                return

            self.applies_to_directories = True

            if not pattern:
                self.match_basename = True
                self.regex = re.compile('.*')
                return

        if '/**/' in pattern:
            first, _, second = pattern.partition('/**/')
            if self._has_glob(first + second):
                self.invalid = "Too complex"
                return

            # Provide regex representing "foo/**/bar"
            self.regex = re.compile('%s(/.*/|/)?%s' % (re.escape(first), re.escape(second)))
            return

        if '**' in pattern:
            self.invalid = "Not supported"
            return

        if pattern.startswith('/'):
            # We're doing matching
            self.match_basename = False
            pattern = pattern[1:]

        elif '/' not in pattern:
            self.match_basename = True

        if self._has_glob(pattern):
            pattern = fnmatch.translate(pattern)
            self.regex = re.compile(pattern)
            return

        self.exact_match = pattern

    def __repr__(self):
        return self.pattern

    def _has_glob(self, pattern):
        """
          :return bool: True if pattern have a shell glob (that can be turned into a regex by fnmatch.translate())
        """
        return '*' in pattern or '?' in pattern

    def match(self, full_path, is_dir=None):
        """
          :param str full_path: Full path to file or folder
          :param bool|None is_dir: If applicable, caller can indicate wether 'full_path' is a directory or not (to save a file stat call)
          :return bool: True if 'full_path' is an ignore-match by this pattern, False otherwise
        """
        assert not self.invalid

        if self.applies_to_directories:
            if is_dir is None:
                is_dir = os.path.isdir(full_path)

            if not is_dir:
                return False

        if self.match_basename:
            name = os.path.basename(full_path)

        else:
            name = full_path[len(self.root) + 1:]

        if self.exact_match:
            return name == self.exact_match

        assert self.regex
        return self.regex.match(name)


def get_filesystem_encoding():
    return sys.getfilesystemencoding() or sys.getdefaultencoding()


def filename_to_ui(value):
    if isinstance(value, bytes):
        value = value.decode(get_filesystem_encoding(), 'replace')
    else:
        value = value.encode('utf-8', 'surrogateescape') \
            .decode('utf-8', 'replace')
    return value


class RepoParamType(click.ParamType):
    name = 'repo'

    def convert(self, value, param, ctx):
        rv = value
        rv = os.path.realpath(rv)

        try:
            st = os.stat(rv)

            file_name = filename_to_ui(value)
            if not stat.S_ISDIR(st.st_mode):
                self.fail(f'"{file_name}" is not a directory.', param, ctx)

            if not os.access(value, os.W_OK):
                self.fail(f'"{file_name}" is not writable.', param, ctx)

            if not os.access(value, os.R_OK):
                self.fail(f'"{file_name}" is not readable.', param, ctx)
        except OSError:
            pass

        try:
            return Repo(rv)
        except NotGitRepository as e:
            return Repo.init(rv, mkdir=True)


def list_files(mypath):
    """Lists all files in given path ignoring files specified by .contentignore"""
    all_files = []
    ignored_files = IgnoredFiles(mypath)
    for root, dirs, files in os.walk(mypath):
        ignored_files.remove_ignored_folders(root, dirs)

        for filename in files:
            fpath = os.path.join(root, filename)
            if ignored_files.match(fpath, is_dir=False):
                continue
            else:
                all_files.append(fpath.replace(mypath + '/', ''))
    return all_files


def add(content_repo, custom_content_repo, paths):
    """Add files to the custom content repo and stage them."""
    for p in paths:
        dst = os.path.join(custom_content_repo.path, p)
        src = os.path.join(content_repo.path, p)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        copyfile(src, dst)

    with open_repo_closing(custom_content_repo) as r:
        r.stage(paths)


def update_content():
    """Fetches the latest changes from `demisto-content`"""
    try:
        clone(DEMISTO_CONTENT_URL, DEMISTO_CONTENT_DIR)
    except FileExistsError as e:
        pass

    return Repo(DEMISTO_CONTENT_DIR)


def determine_version(repo):
    """Determines the correct next version"""
    tags = [x.decode('utf-8') for x in tag_list(repo)]
    tags.sort(key=lambda s: [int(u) for u in s.split('.')])

    today = date.today()
    this_year = str(today.year)[2:]  # ignore the millennium

    index = 0
    if tags:
        year, month, index = tags[-1].split('.')
        if year == this_year:
            if month == str(today.month):
                index = int(index) + 1

    return f'{this_year}.{today.month}.{index}'


def calculate_diff(a, b):
    """Creates a unified diff between two files."""
    with open(a, 'rb') as cp:
        with open(b, 'rb') as cc:
            src_lines = [x.decode('utf-8', 'ignore') for x in list(cp)]
            dst_lines = [x.decode('utf-8', 'ignore') for x in list(cc)]
            return list(difflib.unified_diff(src_lines, dst_lines))


def create_release(custom_content_repo):
    """Creates a new release by committing and tagging changes."""
    version = determine_version(custom_content_repo)
    commit(custom_content_repo, b'Demisto custom content sync.')
    tag_create(custom_content_repo, version, message=b'Automatic release based on demisto-content update.')
    return version


def confirm(msg, force=None, default=False):
    """Wrapper to allow confirmations to be forced."""
    if force:
        return True

    return click.confirm(msg, default=default)


def sync(custom_content_repo, force=None):
    """Syncs content between `demisto-content` and a custom repository."""
    click.secho('Ensuring that demisto content is up to date... ', nl=False)
    content_repo = update_content()
    click.secho('Done!', fg='green')

    click.secho('Filtering ignored files... ', nl=False)
    content_files = list_files(content_repo.path)
    custom_content_files = list_files(custom_content_repo.path)
    click.secho('Done!', fg='green')

    add_all = False
    modify_all = False
    staged_files = []
    for partial_path in content_files:
        if partial_path not in custom_content_files:
            click.secho(f'{partial_path} ', nl=False)
            click.secho('New!', fg='green')

            if add_all:
                staged_files.append(partial_path)
                continue

            if confirm('Do you want to add this file?', force=force, default=True):
                staged_files.append(partial_path)
                if confirm('Do you want to add all new files?', force=force):
                    add_all = True
        else:
            content_path = os.path.join(content_repo.path, partial_path)
            custom_path = os.path.join(custom_content_repo.path, partial_path)

            diff = calculate_diff(content_path, custom_path)

            if diff:
                click.secho(f'{partial_path} ', nl=False)
                click.secho('Modified!', fg='yellow')

                if modify_all:
                    staged_files.append(partial_path)
                    continue

                if confirm('Do you want to view diff?', force=force):
                    for d in diff:
                        if d.startswith('-'):
                            click.secho(d, fg='red', nl=False)
                        elif d.startswith('+'):
                            click.secho(d, fg='green', nl=False)
                        else:
                            click.echo(d, nl=False)

                if confirm('Do you want to accept these changes?', force=force, default=True):
                    staged_files.append(partial_path)
                    if confirm('Do you want to add all modified files?', force=force):
                        add_all = True

    add(content_repo, custom_content_repo, staged_files)

    if len(staged_files):
        click.echo(f'{len(staged_files)} files have been staged.')
        if confirm('Do you want to create a new release with these changes?', force=force, default=True):
            version = create_release(custom_content_repo)
            click.echo('A new released has been created. Tag: ', nl=False)
            click.secho(version, fg='green')
    else:
        click.echo('No files new files added to stage.')

    click.secho('Content sync complete.', fg='green')


@click.group()
@click.version_option(version=__version__)
def integrator_cli():
    pass


@integrator_cli.command(name='sync', help=sync.__doc__)
@click.option('--custom-content-repo', type=RepoParamType(), default=os.path.join(os.getcwd(), 'demisto-custom-content'))
@click.option('--force', is_flag=True)
def sync_cmd(custom_content_repo, force):
    return sync(custom_content_repo, force=force)


def entry_point():
    """Entrypoint that CLI is executed from."""
    integrator_cli()


if __name__ == '__main__':
    entry_point()
