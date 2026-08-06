"""V3 Code Agent —— Streamlit 通过 Docker 沙箱跑 Claude Code CLI

工作流：
  1. 用户在页面 09 提问
  2. 这里 subprocess.Popen 起 `docker run --rm so-codeagent:latest claude -p "..."`
  3. 用 `--output-format stream-json` 读流式 JSON 事件
  4. yield 给 Streamlit 实时渲染
  5. 完事把这一轮写到 code_agent_log 表

多轮对话：
  - 每个 ui_session_id（streamlit 端会话 ID）对应一个 host 持久化目录：
      v3/sandbox-output/{ui_session_id}/_claude_state/
    挂到容器 /home/sandbox/.claude/projects/-sandbox-work/，让 Claude Code CLI
    自己管 session 文件
  - 第一轮跑 `claude -p "..."`，从 system 事件捞 claude_session_id 存到 session_state
  - 后续轮跑 `claude --resume {claude_session_id} -p "..."`

stderr 死锁修复：
  - 早期版本在 proc.wait() 后才 read stderr，pipe 满 64KB 会卡死
  - 现在用 threading.Thread 在主循环旁同时 drain stdout 和 stderr

环境变量：
  ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
配置存 ~/.so_data_analytics/anthropic_config.json （chmod 600）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator, Optional


# ══════════════════════════════════════════════
# 配置持久化
# ══════════════════════════════════════════════

CONFIG_DIR = Path.home() / '.so_data_analytics'
CONFIG_PATH = CONFIG_DIR / 'anthropic_config.json'

DEFAULT_IMAGE = 'so-codeagent:latest'
DEFAULT_TIMEOUT = 1800  # 30 分钟，复杂分析 + LLM 慢响应都够用（原 10 分钟太短）


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass  # Windows 不支持，忽略


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get('base_url') and cfg.get('auth_token') and cfg.get('model'))


# ══════════════════════════════════════════════
# 环境检查
# ══════════════════════════════════════════════

def docker_available() -> tuple[bool, str]:
    """检查 docker 命令 + daemon 是否就绪"""
    if not shutil.which('docker'):
        return False, "docker 命令未找到，请先装 Docker"

    try:
        r = subprocess.run(
            ['docker', 'info'],
            capture_output=True, timeout=10, text=True,
        )
        if r.returncode != 0:
            return False, f"docker daemon 未启动：{r.stderr.strip()[:200]}"
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "docker info 超时（10s），daemon 可能卡死"
    except Exception as e:
        return False, f"docker info 失败：{e}"


def image_exists(image: str = DEFAULT_IMAGE) -> bool:
    """检查沙箱镜像是否已构建"""
    try:
        r = subprocess.run(
            ['docker', 'image', 'inspect', image],
            capture_output=True, timeout=5, text=True,
        )
        return r.returncode == 0
    except Exception:
        return False


# ══════════════════════════════════════════════
# 沙箱运行
# ══════════════════════════════════════════════

def _drain_to_queue(stream, q: Queue, tag: str):
    """后台 thread：把 stream 的每行打 tag 后塞进 queue。EOF 后塞 None。"""
    try:
        for line in iter(stream.readline, ''):
            if not line:
                break
            q.put((tag, line.rstrip('\n')))
    except Exception as e:
        q.put((tag, f'__EXC__:{e}'))
    finally:
        q.put((tag, None))  # EOF 哨兵
        try:
            stream.close()
        except Exception:
            pass


def run_agent(
    question: str,
    *,
    db_path: Path,
    output_root: Path,
    ui_session_id: Optional[str] = None,
    resume_claude_session_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    image: str = DEFAULT_IMAGE,
    max_turns: int = 30,
    memory: str = '2g',
    cpus: str = '2',
    log_to_db: bool = True,
) -> Iterator[dict]:
    """流式跑沙箱 Code Agent。

    Args:
        question: 用户提问
        db_path: SQLite 数据库路径（只读挂入沙箱）
        output_root: 输出根目录（每个 ui_session_id 一个子目录）
        ui_session_id: streamlit 端会话 ID。同 ID 共享 _claude_state，可 resume
        resume_claude_session_id: 若给值则跑 `claude --resume {id}`；否则新会话
        timeout: 超时秒数
        image: 沙箱镜像名
        max_turns: claude 最大工具调用轮数
        memory: 容器内存限制（'2g' 等）
        cpus: 容器 CPU 限制
        log_to_db: 是否写 code_agent_log 表

    yield 的事件 dict 至少有 `type` 字段：
      - 'meta'      —— 启动元信息（ui_session_id / claude_session_id_resumed / cmd / output_dir）
      - 'system'    —— claude 初始化（含 claude session_id）
      - 'assistant' —— 模型输出（文本或 tool_use）
      - 'user'      —— tool_result 回写
      - 'result'    —— 最终结果（cost/duration）
      - 'raw'       —— 解析失败的原始行
      - 'stderr'    —— 容器 stderr（实时增量）
      - 'done'      —— 进程退出（含 claude_session_id / files / log_id）
      - 'error'     —— 异常
    """
    ui_session_id = ui_session_id or uuid.uuid4().hex[:12]
    output_dir = (output_root / ui_session_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # _claude_state/ 持久化 Claude Code CLI 的 session 文件，让多轮对话可 resume
    claude_state_dir = output_dir / '_claude_state'
    claude_state_dir.mkdir(parents=True, exist_ok=True)

    # 容器里 sandbox 用户 uid=1000，host 上 streamlit 进程 uid 可能不同
    # 把 output_dir / _claude_state 设成对所有人可写，避免 PermissionError
    try:
        os.chmod(output_dir, 0o777)
        os.chmod(claude_state_dir, 0o777)
    except OSError:
        pass  # Windows 等不支持 POSIX 权限就跳过

    cfg = load_config()
    base_url = cfg.get('base_url', '')
    auth_token = cfg.get('auth_token', '')
    model = cfg.get('model', '')
    small_model = cfg.get('small_model', model)  # 没配 small_model 就用主模型

    db_path = Path(db_path).resolve()
    if not db_path.exists():
        yield {'type': 'error', 'error': f"DB 文件不存在: {db_path}"}
        return

    # 镜像里 baked 的 CLAUDE.md 可能落后于宿主机最新的 agent-claude.md
    # mount 宿主机最新版覆盖 /sandbox/CLAUDE.md，这样改文档不用 rebuild 镜像
    agent_md_host = Path(__file__).parent.parent / 'v3' / 'agent-claude.md'
    agent_md_mount = []
    if agent_md_host.exists():
        agent_md_mount = ['-v', f'{agent_md_host.resolve()}:/sandbox/CLAUDE.md:ro']

    # SKILL 目录（page 10 月报、未来的周报校验等都放这里）
    # mount 宿主机的 v3/skills/ 进容器，AI 按 prompt 提示找到对应 SKILL.md
    skills_host = Path(__file__).parent.parent / 'v3' / 'skills'
    skills_mount = []
    if skills_host.exists():
        skills_mount = ['-v', f'{skills_host.resolve()}:/sandbox/skills:ro']

    cmd = [
        'docker', 'run', '--rm', '-i',
        '--name', f'codeagent-{ui_session_id}-{int(time.time())}',
        f'--memory={memory}',
        f'--cpus={cpus}',
        '--network=bridge',
        # DB 只读
        '-v', f'{db_path}:/sandbox/db.sqlite:ro',
        # 输出读写
        '-v', f'{output_dir}:/sandbox/output',
        # Claude Code CLI 的 session 状态（持久化以便 resume）
        # 容器里 sandbox 用户家目录是 /home/sandbox
        '-v', f'{claude_state_dir}:/home/sandbox/.claude',
        # 宿主机 agent-claude.md 覆盖容器内 baked 版（改文档不用重 build 镜像）
        *agent_md_mount,
        # SKILL 目录 mount（page 10 月报等 agent 工作流）
        *skills_mount,
        # 端点配置
        '-e', f'ANTHROPIC_BASE_URL={base_url}',
        '-e', f'ANTHROPIC_AUTH_TOKEN={auth_token}',
        '-e', f'ANTHROPIC_API_KEY={auth_token}',  # 双保险，部分端点读 API_KEY
        '-e', f'ANTHROPIC_MODEL={model}',
        '-e', f'ANTHROPIC_SMALL_FAST_MODEL={small_model}',
        # MiniMax 推荐配置（M2.7 等模型回得慢，需要长超时；并强制 Sonnet/Opus/Haiku 别名也走主模型）
        # 文档：https://platform.minimaxi.com/docs/token-plan/claude-code
        '-e', 'API_TIMEOUT_MS=3000000',
        '-e', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1',
        '-e', f'ANTHROPIC_DEFAULT_SONNET_MODEL={model}',
        '-e', f'ANTHROPIC_DEFAULT_OPUS_MODEL={model}',
        '-e', f'ANTHROPIC_DEFAULT_HAIKU_MODEL={model}',
        # 关掉 telemetry / 错误上报，避免泄露给 Anthropic
        '-e', 'DISABLE_TELEMETRY=1',
        '-e', 'DISABLE_ERROR_REPORTING=1',
        '-e', 'DISABLE_AUTOUPDATER=1',
        # 镜像
        image,
        # claude code 命令
        'claude',
        '--print',
        '--output-format', 'stream-json',
        '--verbose',
        '--dangerously-skip-permissions',
        '--max-turns', str(max_turns),
    ]
    if resume_claude_session_id:
        cmd.extend(['--resume', resume_claude_session_id])
    cmd.append(question)

    yield {
        'type': 'meta',
        'ui_session_id': ui_session_id,
        'resumed': bool(resume_claude_session_id),
        'resume_claude_session_id': resume_claude_session_id,
        'output_dir': str(output_dir),
        'image': image,
        'cmd': cmd,
        'db_path': str(db_path),
        'started_at': time.time(),
    }

    # ── 启动 ─────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            bufsize=1,                  # 行缓冲
            start_new_session=True,     # 独立 session/进程组，与父进程信号隔离
        )
    except FileNotFoundError as e:
        yield {'type': 'error', 'error': f'docker 启动失败：{e}'}
        return

    # ── 异步收 stdout / stderr 进同一个 queue，避免 pipe 死锁 ─────
    q: Queue = Queue()
    t_out = threading.Thread(
        target=_drain_to_queue, args=(proc.stdout, q, 'stdout'), daemon=True,
    )
    t_err = threading.Thread(
        target=_drain_to_queue, args=(proc.stderr, q, 'stderr'), daemon=True,
    )
    t_out.start()
    t_err.start()

    # ── 主循环 ─────────────────────────────────
    start = time.time()
    eofs = 0  # 累计收到几路 EOF（stdout + stderr 各一路）
    final_result_event = None
    claude_session_id = None
    assistant_texts: list[str] = []
    tool_calls_summary: list[dict] = []
    last_error: Optional[str] = None
    timed_out = False

    try:
        while eofs < 2:
            try:
                tag, payload = q.get(timeout=0.5)
            except Empty:
                # 超时检查
                if time.time() - start > timeout:
                    timed_out = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    yield {'type': 'error', 'error': f'超时（>{timeout}s）已强制终止'}
                    last_error = f'timeout>{timeout}s'
                    break
                continue

            # 哨兵：某一路 EOF
            if payload is None:
                eofs += 1
                continue

            # 异常哨兵
            if isinstance(payload, str) and payload.startswith('__EXC__:'):
                yield {'type': 'error', 'error': f'{tag} 读取异常：{payload[8:]}'}
                last_error = payload[8:]
                continue

            if tag == 'stderr':
                # stderr 每行实时 yield 一个 stderr 事件，UI 可立刻看到
                if payload.strip():
                    yield {'type': 'stderr', 'text': payload}
                continue

            # tag == 'stdout' → 解析 JSON 事件
            if not payload.strip():
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                yield {'type': 'raw', 'text': payload}
                continue

            etype = event.get('type')

            # 捕获 claude session_id（用于下一轮 resume）
            if etype == 'system' and not claude_session_id:
                claude_session_id = event.get('session_id')

            # 收集文本 / tool_use 用于日志
            if etype == 'assistant':
                msg = event.get('message') or {}
                content = msg.get('content') or []
                if isinstance(content, list):
                    for blk in content:
                        if not isinstance(blk, dict):
                            continue
                        if blk.get('type') == 'text':
                            t = blk.get('text', '')
                            if t:
                                assistant_texts.append(t)
                        elif blk.get('type') == 'tool_use':
                            tool_calls_summary.append({
                                'id': blk.get('id'),
                                'name': blk.get('name'),
                                'input': blk.get('input', {}),
                            })

            if etype == 'result':
                final_result_event = event

            yield event

    finally:
        # 收尾：等进程退出
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        # 等线程退完
        t_out.join(timeout=2)
        t_err.join(timeout=2)

    # ── 列出生成的文件 ─────────────────────────
    # 跳过：_claude_state（session 持久化）、work（page 后端给 AI 的输入数据）
    _SKIP_TOP_DIRS = {'_claude_state', 'work'}
    files = []
    if output_dir.exists():
        for p in sorted(output_dir.rglob('*')):
            rel = p.relative_to(output_dir)
            if rel.parts and rel.parts[0] in _SKIP_TOP_DIRS:
                continue
            if p.is_file() and p.name != '.gitkeep':
                files.append({
                    'name': p.name,
                    'rel': str(rel),
                    'abs': str(p),
                    'size': p.stat().st_size,
                })

    duration_s = round(time.time() - start, 2)
    answer_text = '\n'.join(assistant_texts).strip()

    # ── 写日志 ────────────────────────────────
    log_id = None
    if log_to_db:
        try:
            from _ai_log import log_code_agent_turn
            log_id = log_code_agent_turn(
                ui_session_id=ui_session_id,
                claude_session_id=claude_session_id or '',
                resumed=bool(resume_claude_session_id),
                user_question=question,
                answer_text=answer_text,
                tool_calls=tool_calls_summary,
                files=files,
                returncode=proc.returncode if proc.returncode is not None else -1,
                duration_ms=int(duration_s * 1000),
                cost_usd=(final_result_event or {}).get('total_cost_usd'),
                num_turns=(final_result_event or {}).get('num_turns'),
                model=model,
                error=last_error,
                timed_out=timed_out,
            )
        except Exception as e:
            # 日志失败不阻塞主流程
            yield {'type': 'stderr', 'text': f'[code_agent_log] 写入失败：{e}'}

    yield {
        'type': 'done',
        'returncode': proc.returncode,
        'duration_s': duration_s,
        'files': files,
        'output_dir': str(output_dir),
        'ui_session_id': ui_session_id,
        'claude_session_id': claude_session_id,
        'log_id': log_id,
        'timed_out': timed_out,
    }


# ══════════════════════════════════════════════
# 帮助函数：从 stream-json 事件提取人话
# ══════════════════════════════════════════════

def extract_text(event: dict) -> Optional[str]:
    """从 stream-json 事件里抽出可显示的文本（如果有）"""
    etype = event.get('type')

    if etype == 'assistant':
        msg = event.get('message') or {}
        content = msg.get('content') or []
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for blk in content:
                if isinstance(blk, dict) and blk.get('type') == 'text':
                    parts.append(blk.get('text', ''))
            return '\n'.join(p for p in parts if p) or None

    return None


def extract_tool_use(event: dict) -> Optional[dict]:
    """从 assistant 事件里抽出 tool_use 块（用了哪个工具、参数是什么）"""
    if event.get('type') != 'assistant':
        return None
    msg = event.get('message') or {}
    content = msg.get('content') or []
    if not isinstance(content, list):
        return None
    for blk in content:
        if isinstance(blk, dict) and blk.get('type') == 'tool_use':
            return {
                'id': blk.get('id'),
                'name': blk.get('name'),
                'input': blk.get('input', {}),
            }
    return None


def extract_tool_result(event: dict) -> Optional[dict]:
    """从 user 事件里抽 tool_result（工具回填结果）"""
    if event.get('type') != 'user':
        return None
    msg = event.get('message') or {}
    content = msg.get('content') or []
    if not isinstance(content, list):
        return None
    for blk in content:
        if isinstance(blk, dict) and blk.get('type') == 'tool_result':
            return {
                'tool_use_id': blk.get('tool_use_id'),
                'content': blk.get('content'),
                'is_error': blk.get('is_error', False),
            }
    return None


def extract_result(event: dict) -> Optional[dict]:
    """从 result 事件抽出最终统计"""
    if event.get('type') != 'result':
        return None
    return {
        'subtype': event.get('subtype'),
        'duration_ms': event.get('duration_ms'),
        'duration_api_ms': event.get('duration_api_ms'),
        'num_turns': event.get('num_turns'),
        'cost_usd': event.get('total_cost_usd'),
        'result': event.get('result'),
    }


# ══════════════════════════════════════════════
# 后台异步任务模型（解耦 streamlit 渲染线程 — 用户登出/关 tab 不影响运行）
#
# 设计：
#   start_async_run() 起非 daemon 线程跑 run_agent，状态 + 事件流写入文件：
#     {output_root}/{run_id}/_status.json    当前状态（pending/running/completed/failed）
#     {output_root}/{run_id}/_events.jsonl  每行一个 yield 出的 event
#   页面用 run_id 轮询 get_async_run_status() 即可，无需持有 generator。
#
# 因此：
#   - 即使用户关 tab / 登出 / 切 page，后台线程继续跑（daemon=False）
#   - 用户回来时通过 run_id 续看进度与结果
#   - 容器（docker run）也是后台线程在跑，stdin 不依赖前端
# ══════════════════════════════════════════════

import threading as _threading


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _append_event(path: Path, event: dict):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')


def start_async_run(
    question: str,
    *,
    db_path: Path,
    output_root: Path,
    ui_session_id: Optional[str] = None,
    resume_claude_session_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    image: str = DEFAULT_IMAGE,
    max_turns: int = 30,
    memory: str = '2g',
    cpus: str = '2',
    log_to_db: bool = True,
) -> dict:
    """启动一个后台 agent 任务，立即返回 run 元信息。

    Returns:
        {
          'run_id':        agent 运行 ID（= ui_session_id，便于复用 output 目录）
          'status_file':   状态文件路径
          'events_file':   事件流文件路径
          'output_dir':    输出目录
        }
    """
    rid = ui_session_id or uuid.uuid4().hex[:12]
    output_dir = (output_root / rid).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    status_file = output_dir / '_status.json'
    events_file = output_dir / '_events.jsonl'

    # 如果已经有 running 状态，拒绝重复启动
    if status_file.exists():
        try:
            cur = json.loads(status_file.read_text(encoding='utf-8'))
            if cur.get('status') == 'running':
                return {
                    'run_id': rid, 'output_dir': str(output_dir),
                    'status_file': str(status_file),
                    'events_file': str(events_file),
                    'already_running': True,
                }
        except Exception:
            pass

    # 写初始 status
    _write_json(status_file, {
        'run_id': rid,
        'status': 'pending',
        'question': question,
        'started_at': time.time(),
        'started_at_iso': time.strftime('%Y-%m-%d %H:%M:%S'),
        'timeout': timeout,
        'resume_claude_session_id': resume_claude_session_id,
    })
    # 清空（或新建）events 文件
    events_file.write_text('', encoding='utf-8')

    def _worker():
        final_state = {'status': 'running', 'started_at': time.time()}
        _write_json(status_file, {**_load_status(status_file), 'status': 'running',
                                  'running_at': time.time()})
        try:
            for ev in run_agent(
                question,
                db_path=db_path,
                output_root=output_root,
                ui_session_id=rid,
                resume_claude_session_id=resume_claude_session_id,
                timeout=timeout,
                image=image,
                max_turns=max_turns,
                memory=memory,
                cpus=cpus,
                log_to_db=log_to_db,
            ):
                _append_event(events_file, ev)
                # 关键事件实时反映到 status.json，UI 轮询更快感知
                if ev.get('type') in ('done', 'error', 'result'):
                    final_state.update({'last_event': ev})
        except Exception as e:
            _append_event(events_file, {'type': 'error', 'error': f'worker 异常: {e}'})
            _write_json(status_file, {**_load_status(status_file),
                                      'status': 'failed',
                                      'error': str(e),
                                      'completed_at': time.time()})
            return

        # 正常完成
        _write_json(status_file, {**_load_status(status_file),
                                  'status': 'completed',
                                  'completed_at': time.time()})

    t = _threading.Thread(target=_worker, daemon=False)
    t.start()

    return {
        'run_id': rid,
        'output_dir': str(output_dir),
        'status_file': str(status_file),
        'events_file': str(events_file),
        'already_running': False,
    }


def _load_status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def get_async_run_status(
    run_id: str,
    output_root: Path,
    *,
    from_event_offset: int = 0,
) -> dict:
    """读 run 状态 + 新事件（增量）

    Args:
        from_event_offset: 已经渲染过的事件行数，本次只返回 ≥ 这个位置的新事件
    Returns:
        {
          'status': 'pending'|'running'|'completed'|'failed'|'not_found',
          'started_at': float,
          'completed_at': float?,
          'question': str?,
          'events': [...],         # 新增事件（从 from_event_offset 开始）
          'total_events': int,     # 累计事件数
          'error': str?,
        }
    """
    output_dir = (output_root / run_id).resolve()
    status_file = output_dir / '_status.json'
    events_file = output_dir / '_events.jsonl'

    if not status_file.exists():
        return {'status': 'not_found', 'events': [], 'total_events': 0}

    status = _load_status(status_file)

    # 读 events.jsonl（增量）
    events: list = []
    total = 0
    if events_file.exists():
        with open(events_file, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total += 1
                if i < from_event_offset:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue

    return {
        'status': status.get('status', 'unknown'),
        'started_at': status.get('started_at'),
        'completed_at': status.get('completed_at'),
        'question': status.get('question'),
        'error': status.get('error'),
        'last_event': status.get('last_event'),
        'events': events,
        'total_events': total,
        'output_dir': str(output_dir),
    }


def list_recent_runs(output_root: Path, limit: int = 50) -> list[dict]:
    """列出近期 run（按 status 时间戳降序）"""
    if not output_root.exists():
        return []
    runs = []
    for d in output_root.iterdir():
        sf = d / '_status.json'
        if not sf.exists():
            continue
        s = _load_status(sf)
        if not s:
            continue
        runs.append({
            'run_id': s.get('run_id', d.name),
            'status': s.get('status'),
            'started_at': s.get('started_at'),
            'completed_at': s.get('completed_at'),
            'question_preview': (s.get('question') or '')[:60],
        })
    runs.sort(key=lambda x: x.get('started_at') or 0, reverse=True)
    return runs[:limit]
