#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import os
import os.path
import pprint
import pygit2
import shelve
import subprocess
import sys

# a list of each remote head which is indexed by this script
# If a commit does not appear in one of these remotes, it is considered "not
# upstream" and cannot be sorted.
# Repositories that come first in the list should be pulling/merging from
# repositories lower down in the list. Said differently, commits should trickle
# up from repositories at the end of the list to repositories higher up. For
# example, network commits usually follow "net-next" -> "net" -> "linux.git".
# (head name, [list of possible remote urls])
head_names = (
    ("linux.git", [
        "git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git",
        "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git",
        "https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux.git",
    ]),
    ("net", [
        "git://git.kernel.org/pub/scm/linux/kernel/git/davem/net.git",
        "https://git.kernel.org/pub/scm/linux/kernel/git/davem/net.git",
        "https://kernel.googlesource.com/pub/scm/linux/kernel/git/davem/net.git",
    ]),
    ("net-next", [
        "git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git",
        "https://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git",
        "https://kernel.googlesource.com/pub/scm/linux/kernel/git/davem/net-next.git",
    ]),
)


def _get_heads(repo):
    """
    Returns (head name, sha1)[]
    """
    heads = []
    remotes = {}
    args = ("git", "config", "--get-regexp", "^remote\..+\.url$",)
    for line in subprocess.check_output(args).splitlines():
        name, url = line.split(None, 1)
        name = name.split(".")[1]
        remotes[url] = name

    for head_name, urls in head_names:
        for url in urls:
            if url in remotes:
                rev = "%s/master" % (remotes[url],)
                try:
                    commit = repo.revparse_single(rev)
                except KeyError:
                    raise Exception("Could not read revision \"%s\", does that "
                                    "remote not have a master branch?" % (rev,))
                heads.append((head_name, str(commit.id),))
                continue

    # According to the urls in head_names, this is not a clone of linux.git
    # Sort according to commits reachable from the current head
    if not heads or heads[0][0] != head_names[0][0]:
        heads = [("HEAD", str(repo.revparse_single("HEAD").id),)]

    return heads


def _rebuild_history(heads):
    processed = []
    history = {}
    args = ["git", "log", "--topo-order", "--reverse", "--pretty=tformat:%H"]
    for head_name, rev in heads:
        sp = subprocess.Popen(args + processed + [rev], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)

        if head_name in history:
            raise Exception("head name \"%s\" is not unique." %
                            (head_name,))

        history[head_name] = [l.strip() for l in sp.stdout.readlines()]

        sp.communicate()
        if sp.returncode != 0:
            raise Exception("git log exited with an error:\n" +
                            "\n".join(history[head_name]))

        processed.append("^%s" % (rev,))

    return history


def _get_cache():
    return shelve.open(os.path.expanduser("~/.cache/git-sort"))


def _get_history(heads):
    """
    cache
        heads[]
            (head name, sha1)
        history[head name][]
            git hash represented as string of 40 characters
    """
    cache = _get_cache()
    try:
        c_heads = cache["heads"]
    except KeyError:
        c_heads = None

    if c_heads != heads:
        history = _rebuild_history(heads)
        cache["heads"] = heads
        cache["history"] = history
    else:
        history = cache["history"]
    cache.close()

    return history


class SortedEntry(object):
    def __init__(self, head_name, value):
        self.head_name = head_name
        self.value = value
    def __repr__(self):
        return "%s = %s" % (self.head_name, pprint.pformat(self.value),)


def git_sort(repo, mapping):
    heads = _get_heads(repo)
    history = _get_history(heads)
    for head_name, rev in heads:
        for commit in history[head_name]:
            try:
                yield SortedEntry(head_name, mapping.pop(commit),)
            except KeyError:
                pass

    return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sort input lines according to the upstream order of "
        "commits that each line represents, with the first word on the line "
        "taken to be a commit id.")
    parser.add_argument("-d", "--dump-heads", action="store_true",
                        help="Print the branch heads used for sorting "
                        "(debugging).")
    args = parser.parse_args()

    try:
        path = os.environ["GIT_DIR"]
    except KeyError:
        path = pygit2.discover_repository(os.getcwd())
    repo = pygit2.Repository(path)

    if args.dump_heads:
        print("Cached heads:")
        cache = _get_cache()
        try:
            c_heads = cache["heads"]
        except KeyError:
            c_heads = None
        pprint.pprint(c_heads)
        print("Current heads:")
        heads = _get_heads(repo)
        pprint.pprint(heads)
        if c_heads == heads:
            action = "Will not"
        else:
            action = "Will"
        print("%s rebuild history" % (action,))
        sys.exit(0)

    lines = {}
    num = 0
    for line in sys.stdin.readlines():
        num = num + 1
        try:
            commit = repo.revparse_single(line.strip().split(None, 1)[0])
        except ValueError:
            print("Error: did not find a commit hash on line %d:\n%s" %
                  (num, line.strip(),), file=sys.stderr)
            sys.exit(1)
        except KeyError:
            print("Error: commit hash on line %d not found in the repository:\n%s" %
                  (num, line.strip(),), file=sys.stderr)
            sys.exit(1)
        h = str(commit.id)
        if h in lines:
            lines[h].append(line)
        else:
            lines[h] = [line]

    print("".join([line for entry in git_sort(repo, lines) for line in
                   entry.value]), end="")

    if len(lines) != 0:
        print("Error: the following entries were not found upstream:",
              file=sys.stderr)
        print("".join([line for line_list in lines.values() for line in
                       line_list]), end="")
        sys.exit(1)
