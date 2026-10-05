#!/usr/bin/env python3
"""Run the native V4 agent with the unchanged official local engine."""
import argparse
import importlib.util
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIT = ROOT/'strategy_review_output/v4_rebuild/local-kit/examples/_local/runner'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', default='L1')
    parser.add_argument('--agent-dir', type=Path, default=ROOT/'agent')
    parser.add_argument('--wallclock', type=float, default=900)
    parser.add_argument('--out', type=Path, default=ROOT/'strategy_review_output/v4_rebuild/local-run')
    parser.add_argument('--env-file', type=Path, help='read model configuration from this private file instead of copying it into an agent snapshot')
    model = parser.add_mutually_exclusive_group()
    model.add_argument('--with-model', dest='with_model', action='store_true',
                       help='enable the model (default); read credentials from agent/.env')
    model.add_argument('--without-model', dest='with_model', action='store_false',
                       help='run deterministic planning without model calls')
    parser.set_defaults(with_model=True)
    args = parser.parse_args()
    if not (KIT/'run_local.py').exists():
        raise SystemExit('Download the official kit first: python tools/fetch_local_kit.py')
    spec = importlib.util.spec_from_file_location('official_local_runner', KIT/'run_local.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if sys.platform == 'win32':
        from tools.windows_transport import transport_class
        runner.JsonlTransport = transport_class()
    build_env = runner.agent_environment
    def agent_env(*values, **keywords):
        env, names = build_env(*values, **keywords)
        if args.env_file:
            extra = runner.load_dotenv(args.env_file)
            env.update({key:value for key,value in extra.items() if key not in runner.PROTECTED_KEYS})
            names = sorted(set(names) | set(extra))
        if os.environ.get('AGENT_TRACE_PATH'):
            env['AGENT_TRACE_PATH'] = os.environ['AGENT_TRACE_PATH']
        env['OBSERVER_MODEL_DISABLED'] = '0' if args.with_model else '1'
        if args.with_model and not (env.get('OPENAI_API_KEY', '').strip()
                                   or env.get('KIMI_API_KEY', '').strip()):
            raise SystemExit('Missing local model key: configure agent/.env from agent/.env.example, '
                             'or use --without-model.')
        return env, names
    runner.agent_environment = agent_env
    card = args.card if Path(args.card).is_dir() else str(KIT.parent/'cards'/args.card)
    return runner.main(['--card', card, '--agent', f'"{sys.executable}" -u agent.py',
                        '--agent-cwd', str(args.agent_dir.resolve()), '--wallclock', str(args.wallclock),
                        '--out', str(args.out.resolve()), '--quiet'])

if __name__ == '__main__':
    raise SystemExit(main())
