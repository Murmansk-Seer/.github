"""A green Actions run is not evidence that a new artifact was published."""
import json
import os
from pathlib import Path
import subprocess

LABELS = {
    'unchanged': '无变化 · 未更新/未发布',
    'updated': '已更新 · 资源或配置已提交',
    'published': '已发布 · 数据库验证通过',
    'failed': '失败 · 请查看失败阶段',
    'cancelled': '已取消 · 未确认完成',
    'blocked': '已阻止 · 前置条件未满足',
    'dry_run': '仅检查 · 未写入',
}


def outcome(jobs, evidence):
    results = [job.get('result') for job in jobs.values()]
    if 'failure' in results:
        return 'failed'
    if 'cancelled' in results:
        return 'cancelled'
    for key in ('dry_run', 'published', 'updated', 'blocked'):
        if evidence.get(key) in (True, 'true'):
            return key
    return 'unchanged'


def main():
    jobs = json.loads(os.environ['REPORT_JOBS'])
    evidence = json.loads(os.environ.get('REPORT_EVIDENCE', '{}'))
    status = outcome(jobs, evidence)
    label = LABELS[status]
    if status == 'failed' and evidence.get('published') in (True, 'true'):
        label = '失败 · 产物已发布，后续步骤未完成'
    failed = status in ('failed', 'blocked')
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'status={status}\nlabel={label}\nfailed={str(failed).lower()}\n')
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    run = os.environ.get('GITHUB_RUN_ID', '')
    lines = [f'## {label}', '', f'本次运行：https://github.com/{repo}/actions/runs/{run}',
             '', '绿色成功仅代表本次检查通过，不代表所有下游已更新。',
             '下游通知成功仅代表已发送，不代表下游构建或发布成功。', '',
             '### 阶段结果', '']
    for name, job in jobs.items():
        lines.append(f'- {name}: {job.get("result", "unknown")}')
    lines += ['', '### 实际执行证据', '', '```json',
              json.dumps(evidence, ensure_ascii=False, indent=2), '```']
    tag = os.environ.get('REPORT_RELEASE_TAG', '')
    if tag:
        result = subprocess.run(
            ['gh', 'api', f'repos/{repo}/releases/tags/{tag}'],
            capture_output=True, text=True, encoding='utf-8', check=False,
        )
        if result.returncode == 0:
            release = json.loads(result.stdout)
            lines += ['', '### 当前 Release（不等于本次新发布）', '', release['html_url']]
            for asset in release.get('assets', []):
                if asset['name'].endswith(('.sqlite', '.sha256')):
                    lines.append(f'- {asset["name"]}: {asset["updated_at"]}')
        else:
            lines += ['', '当前 Release 信息不可读取；不得据此认定已发布。']
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
        summary.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
