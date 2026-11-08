"""Restore/publish isolated pipeline state without committing runtime state to main."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

BRANCH = 'pipeline-state'
DIRECTORY = '.build-state'
DEFAULT_MESSAGE = (
    'state(publication): record verified pipeline checkpoint\n\n'
    'EN reason/scope: Persist runtime checkpoint bytes on the state-only branch. '
    'Verification: expected predecessor and state-only paths are checked; unchanged trees are skipped. '
    'The caller remains responsible for validating the release or notification before invoking publish.\n\n'
    '中文原因／范围：仅在状态分支保存运行检查点字节。'
    '验证：校验预期前驱及状态专属路径，相同文件树跳过。'
    '调用方仍须在执行publish之前验证发布或通知。'
)


def git(*args, input=None, env=None, check=True):
    result = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', *args], input=input, env=env, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace'))
    return result


def output(*args):
    return git(*args).stdout.decode().strip()


def marker():
    return Path(output('rev-parse', '--git-path', 'pipeline-state-base'))


def remote_head():
    line = output('ls-remote', '--heads', 'origin', 'refs/heads/' + BRANCH)
    if not line:
        return ''
    git('fetch', '--no-tags', 'origin', 'refs/heads/' + BRANCH)
    return output('rev-parse', 'FETCH_HEAD')


def entries(revision, *, state_subtree=False):
    if not revision:
        return []
    result = []
    args = ['ls-tree', '-r', '-z', revision]
    if state_subtree:
        args.extend(['--', DIRECTORY])
    for item in git(*args).stdout.split(b'\0'):
        if not item:
            continue
        meta, path = item.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        name = path.decode()
        if mode not in ('100644', '100755') or kind != 'blob' or not name.startswith(DIRECTORY + '/') or '..' in Path(name).parts:
            raise ValueError('State branch contains a non-state path: ' + name)
        result.append((name, blob))
    return result


def restore():
    head = remote_head()
    # Before the first state-branch publication, retain only exact previously
    # tracked state. Untracked workspace receipts are never trusted as a base.
    paths = entries(head) if head else entries(output('rev-parse', 'HEAD'), state_subtree=True)
    directory = Path(DIRECTORY)
    if directory.is_symlink():
        raise ValueError('State directory cannot be a symbolic link')
    if directory.exists():
        for existing in directory.rglob('*'):
            if existing.is_symlink():
                raise ValueError('State directory contains a symbolic link')
        for existing in directory.rglob('*'):
            if existing.is_file():
                existing.unlink()
    for name, blob in paths:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(git('cat-file', 'blob', blob).stdout)
    marker().write_text(head, encoding='utf-8')
    return head


def publish(message=DEFAULT_MESSAGE):
    if not marker().exists():
        raise RuntimeError('Restore state before publishing a checkpoint')
    expected = marker().read_text(encoding='utf-8').strip()
    temporary_directory = tempfile.TemporaryDirectory(prefix='pipeline-state-index-')
    temporary = Path(temporary_directory.name) / 'index'
    env = dict(os.environ, GIT_INDEX_FILE=str(temporary.resolve()))
    try:
        for _ in range(3):
            remote = remote_head()
            entries(remote)
            if remote != expected:
                raise RuntimeError('Remote pipeline state changed; rerun from a fresh checkpoint')
            git('read-tree', '--empty', env=env)
            directory = Path(DIRECTORY)
            if directory.is_symlink():
                raise ValueError('State directory cannot be a symbolic link')
            for path in sorted(directory.rglob('*')) if directory.exists() else []:
                if path.is_symlink():
                    raise ValueError('State file cannot be a symbolic link')
                if not path.is_file():
                    continue
                blob = output('hash-object', '-w', str(path))
                git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path.as_posix(), env=env)
            tree = git('write-tree', env=env).stdout.decode().strip()
            if remote and tree == output('rev-parse', remote + '^{tree}'):
                return remote
            parent = ['-p', remote] if remote else []
            committed = git('-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com', 'commit-tree', tree, *parent, input=(message + '\n').encode()).stdout.decode().strip()
            result = git('push', 'origin', committed + ':refs/heads/' + BRANCH, check=False)
            if result.returncode == 0:
                marker().write_text(committed, encoding='utf-8')
                return committed
            observed = remote_head()
            if observed == committed:
                marker().write_text(committed, encoding='utf-8')
                return committed
        raise RuntimeError('State publication failed after three attempts')
    finally:
        temporary_directory.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['restore', 'publish'], required=True)
    args = parser.parse_args()
    revision = restore() if args.mode == 'restore' else publish(os.environ.get('STATE_MESSAGE') or DEFAULT_MESSAGE)
    print(json.dumps({'branch': BRANCH, 'revision': revision, 'mode': args.mode}))
